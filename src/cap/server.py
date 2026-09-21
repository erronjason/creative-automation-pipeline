"""Local web UI: `cap serve`.

A thin FastAPI layer over the same Pipeline the CLI uses. Binds to 127.0.0.1 by default; API
keys stay server-side in .env and never reach the browser.
"""

from __future__ import annotations

import io
import threading
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path
from urllib.parse import urlsplit

import yaml
from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from PIL import Image
from pydantic import BaseModel, ValidationError
from starlette.middleware.trustedhost import TrustedHostMiddleware

from .brand import load_brand
from .brief import SLUG, parse_brief
from .compliance import check_copy, load_rules
from .events import EventLog
from .localize import TRANSLATORS, resolve_copy
from .manifest import Manifest, Review, now
from .pipeline import Pipeline, RunOptions
from .providers import REGISTRY
from .report import render_report
from .storage import LocalStorage


@dataclass
class RunState:
    id: str
    status: str = "running"
    events: list = field(default_factory=list)
    campaign_id: str = ""
    error: str = ""


class ValidateReq(BaseModel):
    file: str | None = None
    text: str | None = None


class RunReq(ValidateReq):
    provider: str = "mock"
    reframe: str = "auto"
    translator: str = "auto"


class ReviewReq(BaseModel):
    variant_id: str
    state: str
    note: str = ""


LOOPBACK_HOSTS = ("127.0.0.1", "localhost")


def create_app(
    base: RunOptions, briefs_dir: str = "briefs", allowed_hosts: tuple[str, ...] = LOOPBACK_HOSTS
) -> FastAPI:
    app = FastAPI(title="Creative Automation Pipeline", docs_url="/api/docs")
    # The UI can spend API credits and write files, so a web page in the user's browser must not be able
    # to drive it. Host allow-listing stops DNS rebinding; the Origin check stops cross-site form posts
    # (a multipart POST is a "simple" request: the browser sends it without any preflight).
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=list(allowed_hosts))

    @app.middleware("http")
    async def same_origin_only(request, call_next):
        origin = request.headers.get("origin")
        cross_origin = origin is not None and urlsplit(origin).netloc != request.headers.get("host")
        if cross_origin and request.method not in ("GET", "HEAD", "OPTIONS"):
            return JSONResponse({"detail": "cross-origin request refused"}, status_code=403)
        return await call_next(request)

    briefs = Path(briefs_dir).resolve()
    out = LocalStorage(base.output)
    out.root.mkdir(parents=True, exist_ok=True)
    assets = LocalStorage(base.assets)
    runs: dict[str, RunState] = {}
    lock = threading.Lock()  # one generation run at a time
    review_lock = threading.Lock()  # serialize manifest read-modify-write

    def brief_path(name: str) -> Path:
        p = (briefs / name).resolve()
        if p.parent != briefs or p.suffix not in (".yaml", ".yml", ".json") or not p.exists():
            raise HTTPException(404, f"brief not found: {name}")
        return p

    def manifests() -> list[Manifest]:
        ms = []
        for f in sorted(out.root.glob("*/manifest.json")):
            try:
                ms.append(Manifest.model_validate_json(f.read_text(encoding="utf-8")))
            except ValidationError:
                continue
        return sorted(ms, key=lambda m: m.finished_at, reverse=True)

    @app.get("/", response_class=HTMLResponse)
    def index():
        return render_report([], mode="live", image_base="/files/{campaign}/")

    @app.get("/api/state")
    def state():
        items = []
        for f in sorted(p for p in briefs.glob("*.y*ml") if ".edited." not in p.name):
            try:
                b = parse_brief(f.read_text(encoding="utf-8"))
                items.append({"file": f.name, "name": b.campaign.name, "campaign_id": b.campaign.id})
            except Exception as e:
                items.append({"file": f.name, "name": f"invalid: {str(e)[:60]}", "campaign_id": ""})
        providers = []
        for name, cls in REGISTRY.items():
            st = cls().status()
            providers.append({"name": name, "ready": st.ready, "detail": st.detail})
        return {
            "briefs": items,
            "providers": providers,
            "default_provider": base.provider,
            "campaigns": [m.model_dump() for m in manifests()],
        }

    @app.get("/api/campaigns")
    def campaigns():
        return [m.model_dump() for m in manifests()]

    @app.get("/api/briefs/{name}")
    def get_brief(name: str):
        return {"text": brief_path(name).read_text(encoding="utf-8")}

    def _analyze(req: ValidateReq) -> dict:
        path = brief_path(req.file) if req.file else None
        text = req.text if req.text is not None else path.read_text(encoding="utf-8")
        try:
            b = parse_brief(text)
            brand = load_brand((path.parent if path else briefs) / b.campaign.brand)
        except ValidationError as e:
            errs = [f"{'.'.join(str(x) for x in err['loc'])}: {err['msg']}" for err in e.errors()]
            return {"ok": False, "errors": errs, "assets": []}
        except (ValueError, FileNotFoundError, yaml.YAMLError) as e:
            return {"ok": False, "errors": [str(e)], "assets": []}
        pipe = Pipeline(RunOptions(**{**asdict(base), "provider": "mock"}))
        rows = []
        for p in b.products:
            key = pipe.find_asset(p)
            rows.append(
                {
                    "product_id": p.id,
                    "name": p.name,
                    "found": bool(key),
                    "key": key,
                    "url": f"/assets/{key}" if key else None,
                }
            )
        rules = load_rules(base.legal_rules)
        legal = []
        for loc, cp in resolve_copy(b, brand.voice, None).items():
            for c in check_copy(rules, loc, {"message": cp.message, "cta": cp.cta, "disclaimer": cp.disclaimer}):
                legal.append({"locale": loc, "status": c.status, "detail": c.detail})
        n = len(b.products) * len(b.aspect_ratios) * len(b.locales)
        return {
            "ok": True,
            "errors": [],
            "assets": rows,
            "legal": legal,
            "summary": f"{len(b.products)} products × {len(b.aspect_ratios)} ratios × {len(b.locales)} locales = {n}",
        }

    @app.post("/api/validate")
    def validate(req: ValidateReq):
        return _analyze(req)

    @app.post("/api/assets/{product_id}")
    async def upload(product_id: str, file: UploadFile = File(...)):
        if not SLUG.match(product_id):
            raise HTTPException(400, "invalid product id")
        data = await file.read()
        if len(data) > 25 * 1024 * 1024:
            raise HTTPException(413, "asset larger than 25 MB")
        try:
            img = Image.open(io.BytesIO(data)).convert("RGB")
        except Exception:
            raise HTTPException(400, "not a readable image") from None
        for old in assets.list(product_id):
            if Path(old).stem == "hero":
                assets.local_path(old).unlink()
        buf = io.BytesIO()
        img.save(buf, "PNG")
        assets.write_bytes(f"{product_id}/hero.png", buf.getvalue())
        return {"key": f"{product_id}/hero.png"}

    @app.post("/api/runs")
    def start_run(req: RunReq):
        if req.provider not in REGISTRY:
            raise HTTPException(400, "unknown provider")
        if req.reframe not in ("auto", "crop", "expand") or req.translator not in ("auto", "none", *TRANSLATORS):
            raise HTTPException(400, "invalid option")
        if not lock.acquire(blocking=False):
            raise HTTPException(409, "a run is already in progress")
        try:
            path = brief_path(req.file) if req.file else None
            if req.text is not None:
                stem = path.stem if path else "brief"
                path = briefs / f"{stem.removesuffix('.edited')}.edited.yaml"
                path.write_text(req.text, encoding="utf-8")
            if path is None:
                raise HTTPException(400, "file or text required")
        except BaseException:
            lock.release()
            raise
        rs = RunState(id=uuid.uuid4().hex[:10])
        runs[rs.id] = rs
        opts = RunOptions(
            **{**asdict(base), "provider": req.provider, "reframe": req.reframe, "translator": req.translator}
        )

        def work():
            try:
                ev = EventLog([lambda e: rs.events.append(asdict(e))])
                m = Pipeline(opts, ev).run(path)
                rs.campaign_id, rs.status = m.campaign_id, "done"
            except Exception as e:
                rs.error, rs.status = f"{type(e).__name__}: {e}", "error"
            finally:
                lock.release()

        threading.Thread(target=work, daemon=True).start()
        return {"run_id": rs.id}

    @app.get("/api/runs/{run_id}")
    def run_status(run_id: str, since: int = 0):
        rs = runs.get(run_id)
        if not rs:
            raise HTTPException(404, "unknown run")
        evs = rs.events[since:]
        return {
            "status": rs.status,
            "events": evs,
            "next": since + len(evs),
            "campaign_id": rs.campaign_id,
            "error": rs.error,
        }

    @app.post("/api/campaigns/{campaign_id}/review")
    def review(campaign_id: str, req: ReviewReq):
        if req.state not in ("approved", "rejected", "pending"):
            raise HTTPException(400, "state must be approved, rejected, or pending")
        key = f"{campaign_id}/manifest.json"
        if not SLUG.match(campaign_id) or not out.exists(key):
            raise HTTPException(404, "campaign not found")
        with review_lock:
            m = Manifest.model_validate_json(out.read_bytes(key))
            for v in m.variants:
                if v.id == req.variant_id:
                    v.review = Review(state=req.state, note=req.note[:500], by="local-reviewer", at=now())
                    out.write_bytes(key, m.model_dump_json(indent=2).encode())
                    out.write_bytes(f"{campaign_id}/report.html", render_report([m]).encode())
                    return v.model_dump()
        raise HTTPException(404, "variant not found")

    app.mount("/files", StaticFiles(directory=out.root), name="files")
    Path(base.assets).mkdir(parents=True, exist_ok=True)
    app.mount("/assets", StaticFiles(directory=assets.root), name="assets")
    return app
