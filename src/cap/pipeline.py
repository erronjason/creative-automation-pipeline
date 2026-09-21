"""Pipeline orchestration.

    brief ─► validate ─► localize copy ─► legal preflight ─► resolve/generate heroes
          ─► per (product × ratio): reframe ─► per locale: render ─► compliance checks
          ─► outputs + manifest.json + variants.csv + report.html + events.jsonl

Design rules:
  * Fail fast and cheap: schema, brand files, and legal copy are checked before any paid call.
  * Degrade, don't die: a failed outpaint falls back to cropping; a failed translation falls back
    to source copy. Each fallback is recorded and surfaces as a warning on the affected variants.
  * Everything that costs money is cached by content hash.
  * Compliance flags, it does not silently block: humans decide, with evidence attached.
"""

from __future__ import annotations

import csv
import io
import json
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path

from PIL import Image

from . import __version__
from .brand import load_brand
from .brief import SUPPORTED_RATIOS, Brief, Product, load_brief
from .cache import Cache, cache_key, sha256
from .compliance import LogoDetector, check_copy, check_variant, load_rules
from .events import EventLog
from .imaging.reframe import reframe
from .imaging.render import compose
from .localize import get_translator, resolve_copy
from .manifest import CheckResult, Manifest, ProductResult, Review, Variant, now, worst
from .prompts import expand_prompt, hero_prompt
from .providers import ImageProvider, ProviderError, get_provider
from .report import render_report
from .storage import LocalStorage, Storage, open_storage

IMAGE_EXT = (".png", ".jpg", ".jpeg", ".webp")


@dataclass
class RunOptions:
    provider: str = "mock"
    assets: str = "assets"
    output: str = "output"
    reframe: str = "auto"  # auto | crop | expand
    translator: str = "auto"  # auto | openai | openrouter | none
    legal_rules: str | None = "legal/prohibited_words.yaml"
    cache_dir: str = ".cache"
    use_cache: bool = True
    workers: int = 4
    only_products: list[str] = field(default_factory=list)
    only_ratios: list[str] = field(default_factory=list)
    only_locales: list[str] = field(default_factory=list)


def ratio_dir(r: str) -> str:
    return r.replace(":", "x")


def _png(img: Image.Image) -> bytes:
    b = io.BytesIO()
    img.save(b, "PNG", optimize=True)
    return b.getvalue()


class Pipeline:
    def __init__(self, opts: RunOptions, events: EventLog | None = None, provider: ImageProvider | None = None):
        self.o = opts
        self.ev = events or EventLog()
        self.provider = provider or get_provider(opts.provider)
        self.assets: Storage = open_storage(opts.assets)
        self.out: Storage = open_storage(opts.output)
        self.cache = Cache(opts.cache_dir, opts.use_cache)

    # ------------------------------------------------------------------------------------------
    def run(self, brief_path: str | Path) -> Manifest:
        t0 = time.perf_counter()
        emit = self.ev.emit

        with self.ev.stage("validate"):
            brief, brief_dir = load_brief(brief_path)
            brand = load_brand(brief_dir / brief.campaign.brand)
            rules = load_rules(self.o.legal_rules)
            brief = self._apply_filters(brief)
            st = self.provider.status()
            if not st.ready:
                raise ProviderError(f"provider '{self.provider.name}' is not configured: {st.detail}")
        emit(
            "validate",
            f"brief '{brief.campaign.id}' OK: {len(brief.products)} products × "
            f"{len(brief.aspect_ratios)} ratios × {len(brief.locales)} locales",
            "success",
        )

        m = Manifest(
            run_id=time.strftime("%Y%m%d-%H%M%S") + "-" + uuid.uuid4().hex[:6],
            campaign_id=brief.campaign.id,
            campaign_name=brief.campaign.name,
            brand=brand.name,
            region=brief.target.region,
            audience=brief.target.audience,
            message=brief.campaign.message,
            started_at=now(),
            provider=self.provider.name,
            model=self.provider.model,
            reframe_strategy=self.o.reframe,
            translator="none",
            pipeline_version=__version__,
            brief_sha256=sha256(Path(brief_path).read_bytes()),
            storage=self.out.describe(),
            aspect_ratios=brief.aspect_ratios,
            locales=brief.locales,
        )
        prior_reviews = self._prior_reviews(brief.campaign.id)

        # Copy first: it is cheap, and a legal failure should be visible before spending on images.
        with self.ev.stage("localize"):
            translator = get_translator(self.o.translator)
            m.translator = getattr(translator, "name", "none")
            copies = resolve_copy(brief, brand.voice, translator, emit)
            legal = {
                loc: check_copy(rules, loc, {"message": c.message, "cta": c.cta, "disclaimer": c.disclaimer})
                for loc, c in copies.items()
            }
            for loc, checks in legal.items():
                for c in checks:
                    if c.status != "pass":
                        emit("legal", f"{loc}: {c.detail}", "error" if c.status == "fail" else "warn")

        with self.ev.stage("assets"), ThreadPoolExecutor(self.o.workers) as pool:
            heroes = list(pool.map(lambda p: self._hero(brief, brand, p, m), brief.products))

        markets = {mk.locale: mk.code for mk in brief.target.markets}
        detector = LogoDetector(brand)
        jobs = [(p, h, r) for p, h in zip(brief.products, heroes, strict=True) for r in brief.aspect_ratios]

        def job(args):
            product, hero, ratio = args
            size = SUPPORTED_RATIOS[ratio]
            with self.ev.stage("reframe"):
                rf = reframe(
                    hero,
                    size,
                    strategy=self.o.reframe,
                    provider=self.provider,
                    cache=self.cache,
                    prompt=expand_prompt(brief, product, brand),
                    focus=product.assets.focus,
                )
            emit("reframe", f"{product.id} {ratio}: {rf.method}", "info", detail=rf.detail)
            out = []
            for loc, cp in copies.items():
                with self.ev.stage("render"):
                    img, lay = compose(rf.image, ratio, brand, cp.message, cp.cta, cp.disclaimer, loc)
                with self.ev.stage("compliance"):
                    checks = [*legal[loc], *check_variant(img, lay, brand, detector, rf.upscale)]
                    checks.append(_copy_check(cp))
                data = _png(img)
                rel = f"{product.id}/{ratio_dir(ratio)}/{loc}.png"
                with self.ev.stage("write"):
                    self.out.write_bytes(f"{brief.campaign.id}/{rel}", data, "image/png")
                vid = f"{brief.campaign.id}/{product.id}/{ratio_dir(ratio)}/{loc}"
                digest = sha256(data)
                prev = prior_reviews.get(vid)
                v = Variant(
                    id=vid,
                    product_id=product.id,
                    ratio=ratio,
                    locale=loc,
                    market=markets.get(loc, ""),
                    path=rel,
                    width=img.width,
                    height=img.height,
                    sha256=digest,
                    copy_text={"message": cp.message, "cta": cp.cta, "disclaimer": cp.disclaimer},
                    copy_source=cp.source,
                    reframe={
                        "method": rf.method,
                        "detail": rf.detail,
                        "upscale": round(rf.upscale, 2),
                        "cached": rf.cached,
                    },
                    checks=checks,
                    status=worst(c.status for c in checks),
                    review=prev[1] if prev and prev[0] == digest else Review(),
                )
                out.append(v)
                lvl = {"pass": "success", "warn": "warn", "fail": "error"}[v.status]
                emit("variant", f"{vid} → {v.status}", lvl, path=rel)
            return out

        with ThreadPoolExecutor(self.o.workers) as pool:
            for vs in pool.map(job, jobs):
                m.variants.extend(vs)

        with self.ev.stage("write"):
            self._finalize(m, t0)
            self._write_outputs(m)
        emit(
            "done",
            f"{m.stats.variants} variants: {m.stats.passed} pass, {m.stats.warned} warn, "
            f"{m.stats.failed} fail in {m.stats.duration_s:.1f}s",
            "success",
        )
        return m

    # ------------------------------------------------------------------------------------------
    def _apply_filters(self, brief: Brief) -> Brief:
        b = brief.model_copy(deep=True)
        if self.o.only_products:
            b.products = [p for p in b.products if p.id in self.o.only_products]
        if self.o.only_ratios:
            b.aspect_ratios = [r for r in b.aspect_ratios if r in self.o.only_ratios]
        if self.o.only_locales:
            b.target.markets = [mk for mk in b.target.markets if mk.locale in self.o.only_locales]
        if not (b.products and b.aspect_ratios and b.target.markets):
            raise ValueError("filters removed every product, ratio, or market")
        return b

    def _find_asset(self, product: Product) -> str | None:
        if product.assets.hero:
            if self.assets.exists(product.assets.hero):
                return product.assets.hero
            self.ev.emit("assets", f"{product.id}: brief references missing asset '{product.assets.hero}'", "warn")
        for ext in IMAGE_EXT:
            key = f"{product.id}/hero{ext}"
            if self.assets.exists(key):
                return key
        candidates = [k for k in self.assets.list(product.id) if k.lower().endswith(IMAGE_EXT)]
        return candidates[0] if candidates else None

    def _hero(self, brief: Brief, brand, product: Product, m: Manifest) -> Image.Image:
        key = self._find_asset(product)
        src_key = f"{brief.campaign.id}/{product.id}/_source/hero.png"
        if key:
            img = Image.open(io.BytesIO(self.assets.read_bytes(key))).convert("RGB")
            m.products.append(
                ProductResult(
                    id=product.id,
                    name=product.name,
                    asset_source="reused",
                    hero_path=f"{product.id}/_source/hero.png",
                    detail=f"reused {key}",
                )
            )
            self.ev.emit("assets", f"{product.id}: reusing approved asset {key}", "success")
        else:
            prompt = hero_prompt(brief, product, brand)
            size = self.provider.hero_size
            ck = cache_key(
                op="generate", provider=self.provider.name, model=self.provider.model, prompt=prompt, size=size
            )
            data = self.cache.get(ck)
            source = "cached" if data else "generated"
            if data is None:
                self.ev.emit("assets", f"{product.id}: no asset found, generating with {self.provider.name}…")
                data = self.provider.generate(prompt, size)
                self.cache.put(ck, data)
            img = Image.open(io.BytesIO(data)).convert("RGB")
            m.products.append(
                ProductResult(
                    id=product.id,
                    name=product.name,
                    asset_source=source,
                    hero_path=f"{product.id}/_source/hero.png",
                    prompt=prompt,
                    detail=f"{self.provider.name}/{self.provider.model} {size[0]}x{size[1]}",
                )
            )
            self.ev.emit(
                "assets", f"{product.id}: hero {source}" + (" (cache hit, $0)" if source == "cached" else ""), "success"
            )
        self.out.write_bytes(src_key, _png(img), "image/png")
        return img

    def _prior_reviews(self, campaign_id: str) -> dict[str, tuple[str, Review]]:
        key = f"{campaign_id}/manifest.json"
        if not self.out.exists(key):
            return {}
        try:
            old = Manifest.model_validate_json(self.out.read_bytes(key))
            return {v.id: (v.sha256, v.review) for v in old.variants if v.review.state != "pending"}
        except Exception:
            return {}

    def _finalize(self, m: Manifest, t0: float) -> None:
        m.variants.sort(key=lambda v: (v.product_id, m.aspect_ratios.index(v.ratio), m.locales.index(v.locale)))
        m.products.sort(key=lambda p: p.id)
        s = m.stats
        s.variants = len(m.variants)
        s.passed = sum(v.status == "pass" for v in m.variants)
        s.warned = sum(v.status == "warn" for v in m.variants)
        s.failed = sum(v.status == "fail" for v in m.variants)
        s.heroes_reused = sum(p.asset_source == "reused" for p in m.products)
        s.heroes_generated = sum(p.asset_source in ("generated", "cached") for p in m.products)
        s.genai_calls = self.provider.calls
        s.cache_hits = self.cache.hits
        billed = self.provider.actual_cost_usd()  # real spend when the provider reports it
        s.est_cost_usd = round(
            billed if billed is not None else self.provider.calls * self.provider.est_cost_per_image, 2
        )
        s.est_saved_usd = round(self.cache.hits * self.provider.avg_cost_per_call(), 2)
        s.duration_s = round(time.perf_counter() - t0, 2)
        s.stage_seconds = dict(self.ev.timings)
        m.finished_at = now()
        m.warnings = [e.message for e in self.ev.events if e.level in ("warn", "error") and e.stage != "variant"]

    def _write_outputs(self, m: Manifest) -> None:
        c = m.campaign_id
        self.out.write_bytes(f"{c}/manifest.json", m.model_dump_json(indent=2).encode(), "application/json")
        self.out.write_bytes(f"{c}/variants.csv", variants_csv(m).encode(), "text/csv")
        self.out.write_bytes(f"{c}/report.html", render_report([m], mode="report").encode(), "text/html")
        buf = io.StringIO()
        for e in self.ev.events:
            buf.write(json.dumps(e.__dict__, ensure_ascii=False) + "\n")
        self.out.write_bytes(f"{c}/events.jsonl", buf.getvalue().encode(), "application/x-ndjson")


def _copy_check(cp) -> CheckResult:
    status = {"brief": "pass", "machine": "warn", "fallback": "warn"}[cp.source]
    return CheckResult(id="localization.copy_source", category="localization", status=status, detail=cp.note)


def variants_csv(m: Manifest) -> str:
    """Flat export for joining with ad-platform performance data (CTR, CVR) by variant_id."""
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(
        [
            "variant_id",
            "campaign_id",
            "product_id",
            "market",
            "locale",
            "ratio",
            "asset_source",
            "reframe_method",
            "copy_source",
            "status",
            "review_state",
            "path",
            "message",
        ]
    )
    src = {p.id: p.asset_source for p in m.products}
    for v in m.variants:
        w.writerow(
            [
                v.id,
                m.campaign_id,
                v.product_id,
                v.market,
                v.locale,
                v.ratio,
                src.get(v.product_id, ""),
                v.reframe.get("method", ""),
                v.copy_source,
                v.status,
                v.review.state,
                v.path,
                v.copy_text.get("message", ""),
            ]
        )
    return buf.getvalue()


def local_output_root(opts: RunOptions) -> Path | None:
    s = open_storage(opts.output)
    return s.root if isinstance(s, LocalStorage) else None
