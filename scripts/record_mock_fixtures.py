"""Record real image generations as fixtures for the offline mock provider.

The mock provider replays these instead of drawing placeholders, so the offline demo (`cap demo`) shows the
same images a real run produced. Run it after a real run (`cap run BRIEF --provider openrouter`), while that
run's output folder and result cache are still on disk:

    python scripts/record_mock_fixtures.py                        # every campaign in ./output
    python scripts/record_mock_fixtures.py --campaign summer-refresh-2026 \\
        --campaign afternoon-boost-demo:citrus-electrolyte        # ID, or ID:product,product to pick products
    python scripts/record_mock_fixtures.py --asset sparkling-yuzu  # also install that hero as the approved packshot

What it stores, in src/cap/providers/recorded/:
  heroes/<sha256(prompt)[:16]>.png      generated heroes, lossless, found again by their exact prompt
  expansions/<hero pixels>-<WxH>.jpg    the provider's outpaints, found by the hero's pixels and the canvas size
  index.json                            what is there, and where it came from

Heroes are lossless because an outpaint is looked up by the hero's *pixels*: a lossy hero would change them and
the lookup would miss. Outpaints are JPEG because nothing downstream is keyed on them.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import date
from pathlib import Path

from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from cap.brand import load_brand  # noqa: E402
from cap.brief import SUPPORTED_RATIOS, load_brief  # noqa: E402
from cap.cache import cache_key, sha256  # noqa: E402
from cap.imaging.reframe import _png as reframe_png  # noqa: E402  the exact encoder the cache key hashes
from cap.imaging.reframe import expand_canvas  # noqa: E402
from cap.manifest import Manifest  # noqa: E402
from cap.prompts import expand_prompt  # noqa: E402
from cap.providers.mock import pixel_id, prompt_id  # noqa: E402
from cap.providers.openrouter import OpenRouterProvider  # noqa: E402


def find_brief(campaign_id: str):
    for path in sorted((ROOT / "briefs").glob("*.yaml")):
        if path.name.endswith(".edited.yaml"):
            continue
        brief, folder = load_brief(path)
        if brief.campaign.id == campaign_id:
            return brief, folder
    raise SystemExit(f"no brief in ./briefs has campaign id '{campaign_id}'")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument(
        "--campaign", action="append", default=[], help="ID or ID:product,product (default: all in ./output)"
    )
    ap.add_argument(
        "--asset", action="append", default=[], help="also write this product's hero to assets/<product>/hero.png"
    )
    ap.add_argument("--output", default=str(ROOT / "output"))
    ap.add_argument("--cache", default=str(ROOT / ".cache"))
    ap.add_argument("--dest", default=str(ROOT / "src" / "cap" / "providers" / "recorded"))
    args = ap.parse_args()

    out, cache, dest = Path(args.output), Path(args.cache), Path(args.dest)
    wanted: dict[str, set[str] | None] = {}
    for spec in args.campaign or [p.parent.name for p in sorted(out.glob("*/manifest.json"))]:
        cid, _, products = spec.partition(":")
        wanted[cid] = set(products.split(",")) if products else None

    (dest / "heroes").mkdir(parents=True, exist_ok=True)
    (dest / "expansions").mkdir(parents=True, exist_ok=True)
    provider = OpenRouterProvider()  # only for its cache tag: no key is needed and no call is made
    index = {
        "source": "openai/gpt-image-2 via OpenRouter",
        "recorded": date.today().isoformat(),
        "heroes": {},
        "expansions": {},
    }
    missing = 0

    for cid, only in wanted.items():
        manifest = Manifest.model_validate_json((out / cid / "manifest.json").read_text(encoding="utf-8"))
        if manifest.provider != "openrouter":
            raise SystemExit(f"{cid} was made by '{manifest.provider}', not a real run: nothing to record")
        brief, folder = find_brief(cid)
        brand = load_brand(folder / brief.campaign.brand)
        products = {p.id: p for p in brief.products}
        for pr in manifest.products:
            if only is not None and pr.id not in only:
                continue
            hero = Image.open(out / cid / pr.hero_path).convert("RGB")
            pixels = pixel_id(hero)
            if pr.asset_source in ("generated", "cached") and pr.prompt:
                name = f"{prompt_id(pr.prompt)}.png"
                hero.save(dest / "heroes" / name, "PNG", optimize=True)
                index["heroes"][prompt_id(pr.prompt)] = name
            if pr.id in args.asset:
                target = ROOT / "assets" / pr.id
                target.mkdir(parents=True, exist_ok=True)
                hero.save(target / "hero.png", "PNG", optimize=True)
                for old in target.glob(
                    "hero.[jw]*"
                ):  # an older hero.jpg would be shadowed, but do not leave it lying around
                    old.unlink()
            src_sha = sha256(reframe_png(hero))
            prompt = expand_prompt(brief, products[pr.id], brand)
            for ratio in manifest.aspect_ratios:
                if not any(
                    v.product_id == pr.id and v.ratio == ratio and v.reframe.get("method") == "expand"
                    for v in manifest.variants
                ):
                    continue
                w, h = SUPPORTED_RATIOS[ratio]
                canvas = expand_canvas(hero.size, w / h)
                key = cache_key(
                    op="expand",
                    provider=manifest.provider,
                    model=manifest.model,
                    src=src_sha,
                    canvas=canvas,
                    prompt=prompt,
                    tag=provider.cache_tag,
                )
                raw = cache / key[:2] / f"{key}.png"
                if not raw.exists():
                    print(f"  MISSING  {cid}/{pr.id} {ratio}: no cached outpaint for key {key[:12]}")
                    missing += 1
                    continue
                name = f"{pixels}-{canvas[0]}x{canvas[1]}.jpg"
                Image.open(raw).convert("RGB").save(dest / "expansions" / name, "JPEG", quality=92, optimize=True)
                index["expansions"][f"{pixels}-{canvas[0]}x{canvas[1]}"] = name
                print(f"  recorded {cid}/{pr.id} {ratio} -> {name}")

    (dest / "index.json").write_text(json.dumps(index, indent=2, sort_keys=True), encoding="utf-8")
    size = sum(f.stat().st_size for f in dest.rglob("*") if f.is_file()) / 1e6
    print(f"{len(index['heroes'])} heroes, {len(index['expansions'])} outpaints, {size:.1f} MB in {dest}")
    if missing:
        raise SystemExit(f"{missing} outpaint(s) were not in the cache and were skipped")


if __name__ == "__main__":
    main()
