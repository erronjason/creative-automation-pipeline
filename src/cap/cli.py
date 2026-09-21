"""Command-line interface: `cap run | validate | serve | showcase | providers | demo`."""

from __future__ import annotations

import json
import os
import shutil
import sys
import webbrowser
from pathlib import Path

import typer
from dotenv import load_dotenv
from PIL import Image
from pydantic import ValidationError
from rich.console import Console
from rich.table import Table

from .brand import load_brand
from .brief import load_brief
from .compliance import check_copy, load_rules
from .events import Event, EventLog
from .localize import resolve_copy
from .manifest import Manifest
from .pipeline import Pipeline, RunOptions, local_output_root
from .providers import REGISTRY, ProviderError
from .report import render_report


def _use_utf8(stream) -> None:
    """Redirected output on Windows gets a legacy code page (cp1252) that cannot encode the ✓ and × we print.

    A terminal is unaffected, but `cap validate > log.txt` or a script capturing output would crash.
    """
    if hasattr(stream, "reconfigure") and (stream.encoding or "").lower().replace("-", "") != "utf8":
        stream.reconfigure(encoding="utf-8", errors="replace")


for _stream in (sys.stdout, sys.stderr):
    _use_utf8(_stream)

load_dotenv()
app = typer.Typer(add_completion=False, no_args_is_help=True, help="GenAI creative automation for social ad campaigns.")
con = Console(highlight=False)
STYLE = {"info": "dim", "success": "green", "warn": "yellow", "error": "bold red"}


def _printer(ev: Event) -> None:
    if ev.stage == "variant" and ev.level != "error":
        return  # keep the console readable; every variant is in the manifest and events.jsonl
    con.print(f"[{STYLE.get(ev.level, '')}]{ev.stage:>9}[/] {ev.message}")


def _summary(m: Manifest, out_root: Path | None) -> None:
    t = Table(title=f"{m.campaign_name} · {m.stats.variants} variants", show_lines=False, header_style="bold")
    for col in ["product", "ratio", *m.locales]:
        t.add_column(col)
    color = {"pass": "green", "warn": "yellow", "fail": "red"}
    for p in m.products:
        for r in m.aspect_ratios:
            cells = []
            for loc in m.locales:
                v = next((x for x in m.variants if x.product_id == p.id and x.ratio == r and x.locale == loc), None)
                cells.append(f"[{color[v.status]}]{v.status}[/]" if v else "")
            t.add_row(p.id, r, *cells)
    con.print(t)
    s = m.stats
    con.print(
        f"heroes reused {s.heroes_reused} / generated {s.heroes_generated} · GenAI calls {s.genai_calls} · "
        f"cache hits {s.cache_hits} · {'billed' if s.cost_billed else 'est.'} ${s.est_cost_usd:.2f} "
        f"(saved ${s.est_saved_usd:.2f}) · {s.duration_s:.1f}s"
    )
    if out_root:
        con.print(f"[bold]report[/]  {out_root / m.campaign_id / 'report.html'}")


@app.command()
def run(
    brief: Path = typer.Argument(..., exists=True, dir_okay=False, help="Campaign brief (YAML or JSON)"),
    provider: str = typer.Option("mock", "--provider", "-p", help=f"Image provider: {', '.join(REGISTRY)}"),
    assets: str = typer.Option("assets", help="Input asset root (folder or s3://bucket/prefix)"),
    output: str = typer.Option("output", "--output", "-o", help="Output root (folder or s3://bucket/prefix)"),
    reframe: str = typer.Option("auto", help="auto | crop | expand"),
    translator: str = typer.Option("auto", help="auto | openai | openrouter | none"),
    legal_rules: str = typer.Option("legal/prohibited_words.yaml", help="Legal rules YAML"),
    workers: int = typer.Option(4, min=1, max=16),
    no_cache: bool = typer.Option(False, "--no-cache", help="Bypass the GenAI result cache"),
    product: list[str] = typer.Option([], "--product", help="Only these product ids (repeatable)"),
    ratio: list[str] = typer.Option([], "--ratio", help="Only these ratios (repeatable)"),
    locale: list[str] = typer.Option([], "--locale", help="Only these locales (repeatable)"),
    strict: bool = typer.Option(False, help="Exit 2 if any variant fails compliance (for CI gates)"),
    open_report: bool = typer.Option(False, "--open", help="Open the HTML report when done"),
):
    """Generate every product × ratio × locale variant for a brief."""
    if reframe not in ("auto", "crop", "expand"):
        raise typer.BadParameter("reframe must be auto, crop, or expand")
    opts = RunOptions(
        provider=provider,
        assets=assets,
        output=output,
        reframe=reframe,
        translator=translator,
        legal_rules=legal_rules,
        workers=workers,
        use_cache=not no_cache,
        only_products=product,
        only_ratios=ratio,
        only_locales=locale,
    )
    try:
        m = Pipeline(opts, EventLog([_printer])).run(brief)
    except ValidationError as e:
        con.print(f"[bold red]invalid brief[/] {brief}")
        for err in e.errors():
            con.print(f"  • {'.'.join(str(x) for x in err['loc'])}: {err['msg']}")
        raise typer.Exit(1) from None
    except (ProviderError, ValueError, FileNotFoundError, RuntimeError) as e:
        con.print(f"[bold red]error[/] {e}")
        raise typer.Exit(1) from None
    root = local_output_root(opts)
    _summary(m, root)
    if open_report and root:
        webbrowser.open((root / m.campaign_id / "report.html").as_uri())
    if strict and m.stats.failed:
        raise typer.Exit(2)


@app.command()
def validate(
    brief: Path = typer.Argument(..., exists=True, dir_okay=False),
    legal_rules: str = typer.Option("legal/prohibited_words.yaml"),
):
    """Validate a brief and preflight its copy against legal rules. No GenAI calls."""
    try:
        b, d = load_brief(brief)
        brand = load_brand(d / b.campaign.brand)
    except ValidationError as e:
        con.print(f"[bold red]invalid brief[/] {brief}")
        for err in e.errors():
            con.print(f"  • {'.'.join(str(x) for x in err['loc'])}: {err['msg']}")
        raise typer.Exit(1) from None
    except (ValueError, FileNotFoundError) as e:
        con.print(f"[bold red]error[/] {e}")
        raise typer.Exit(1) from None
    rules = load_rules(legal_rules)
    failed = False
    con.print(
        f"[green]✓[/] {b.campaign.id}: {len(b.products)} products × {len(b.aspect_ratios)} ratios × "
        f"{len(b.locales)} locales = {len(b.products) * len(b.aspect_ratios) * len(b.locales)} variants"
    )
    for loc, cp in resolve_copy(b, brand.voice, None).items():
        checks = check_copy(rules, loc, {"message": cp.message, "cta": cp.cta, "disclaimer": cp.disclaimer})
        worst = max((c for c in checks), key=lambda c: {"pass": 0, "warn": 1, "fail": 2}[c.status])
        color = {"pass": "green", "warn": "yellow", "fail": "red"}[worst.status]
        note = "" if cp.source == "brief" else f" [yellow](no approved copy: {cp.source})[/]"
        con.print(f"  [{color}]{worst.status:>4}[/] {loc}: {cp.message}{note}")
        for c in checks:
            if c.status != "pass":
                con.print(f"       [{color}]{c.detail}[/]")
        failed |= worst.status == "fail"
    raise typer.Exit(1 if failed else 0)


@app.command()
def serve(
    host: str = typer.Option("127.0.0.1"),
    port: int = typer.Option(8765),
    provider: str = typer.Option("mock", help="Default provider selected in the UI"),
    assets: str = typer.Option("assets"),
    output: str = typer.Option("output"),
    briefs: str = typer.Option("briefs"),
    no_open: bool = typer.Option(False, "--no-open"),
):
    """Launch the local web UI (brief editor, asset upload, run log, review & approval)."""
    import threading

    import uvicorn

    from .server import LOOPBACK_HOSTS, create_app

    if output.startswith("s3://"):
        con.print("[red]the web UI serves local output only; use a folder for --output[/]")
        raise typer.Exit(1)
    # Extra hostnames the UI may be reached by (e.g. a LAN name): CAP_ALLOWED_HOSTS=a.local,b.local
    extra = [h.strip() for h in os.getenv("CAP_ALLOWED_HOSTS", "").split(",") if h.strip()]
    hosts = tuple(dict.fromkeys([*LOOPBACK_HOSTS, host, *extra]))
    api = create_app(RunOptions(provider=provider, assets=assets, output=output), briefs, allowed_hosts=hosts)
    url = f"http://{host}:{port}"
    con.print(f"[bold green]Creative Automation Pipeline[/] → {url}   (Ctrl+C to stop)")
    if not no_open:
        threading.Timer(1.2, lambda: webbrowser.open(url)).start()
    uvicorn.run(api, host=host, port=port, log_level="warning")


@app.command()
def showcase(
    campaigns: list[Path] = typer.Argument(..., help="Campaign output folders (containing manifest.json)"),
    dest: Path = typer.Option(Path("showcase"), help="Destination folder (deployed to GitHub Pages)"),
    quality: int = typer.Option(84, min=50, max=95, help="JPEG quality for published images"),
):
    """Export runs as a static, read-only site (no server, no keys) for GitHub Pages."""
    dest.mkdir(parents=True, exist_ok=True)
    ms: list[Manifest] = []
    for c in campaigns:
        mf = c / "manifest.json"
        if not mf.exists():
            con.print(f"[red]no manifest.json in {c}[/]")
            raise typer.Exit(1)
        m = Manifest.model_validate_json(mf.read_text(encoding="utf-8"))
        target = dest / m.campaign_id
        if target.exists():
            shutil.rmtree(target)
        target.mkdir(parents=True)
        rels = [v.path for v in m.variants] + [p.hero_path for p in m.products]
        for rel in dict.fromkeys(rels):
            jpg = str(Path(rel).with_suffix(".jpg")).replace("\\", "/")
            (target / jpg).parent.mkdir(parents=True, exist_ok=True)
            Image.open(c / rel).convert("RGB").save(
                target / jpg, "JPEG", quality=quality, optimize=True, progressive=True
            )
        for v in m.variants:
            v.path = str(Path(v.path).with_suffix(".jpg")).replace("\\", "/")
        for p in m.products:
            p.hero_path = str(Path(p.hero_path).with_suffix(".jpg")).replace("\\", "/")
        for f in ("variants.csv", "events.jsonl"):
            if (c / f).exists():
                shutil.copy(c / f, target / f)
        (target / "manifest.json").write_text(m.model_dump_json(indent=2), encoding="utf-8")
        ms.append(m)
    (dest / "index.html").write_text(render_report(ms, mode="showcase", image_base="{campaign}/"), encoding="utf-8")
    (dest / ".nojekyll").write_text("")
    size = sum(f.stat().st_size for f in dest.rglob("*") if f.is_file()) / 1e6
    con.print(f"[green]✓[/] showcase with {len(ms)} campaign(s) → {dest / 'index.html'} ({size:.1f} MB)")


@app.command()
def schema(which: str = typer.Argument("brief", help="brief | brand")):
    """Print the JSON Schema of a brief or brand file, for editor validation and tooling."""
    from .brand import Brand
    from .brief import Brief

    models = {"brief": Brief, "brand": Brand}
    if which not in models:
        raise typer.BadParameter("choose brief or brand")
    sys.stdout.write(json.dumps(models[which].model_json_schema(), indent=2, ensure_ascii=False) + "\n")


@app.command()
def providers():
    """Show which image providers are configured."""
    t = Table(header_style="bold")
    t.add_column("provider")
    t.add_column("ready")
    t.add_column("detail")
    for name, cls in REGISTRY.items():
        st = cls().status()
        t.add_row(name, "[green]yes[/]" if st.ready else "[yellow]no[/]", st.detail)
    con.print(t)


@app.command()
def demo(
    provider: str = typer.Option("mock", "--provider", "-p"),
    open_report: bool = typer.Option(True, "--open/--no-open"),
):
    """Run every example brief in ./briefs (offline with the mock provider by default)."""
    briefs = sorted(p for p in Path("briefs").glob("*.yaml") if not p.name.endswith(".edited.yaml"))
    if not briefs:
        con.print("[red]no briefs found in ./briefs (run from the repo root)[/]")
        raise typer.Exit(1)
    last = None
    for b in briefs:
        con.rule(f"[bold]{b.name}")
        opts = RunOptions(provider=provider)
        try:
            last = Pipeline(opts, EventLog([_printer])).run(b)
        except (ProviderError, RuntimeError) as e:
            con.print(f"[red]{e}[/]")
            raise typer.Exit(1) from None
        _summary(last, local_output_root(opts))
    if open_report and last:
        webbrowser.open((Path("output").resolve() / last.campaign_id / "report.html").as_uri())


def main() -> None:  # pragma: no cover
    app()


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
