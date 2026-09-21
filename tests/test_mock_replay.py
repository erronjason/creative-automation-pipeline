"""The mock replays real gpt-image-2 output for the demo briefs, and falls back to placeholders for anything else."""

import io
from pathlib import Path

import numpy as np
from PIL import Image

from cap.brand import load_brand
from cap.brief import load_brief
from cap.pipeline import Pipeline
from cap.prompts import hero_prompt
from cap.providers.mock import MockProvider, pixel_id, prompt_id, recorded

ROOT = Path(__file__).resolve().parents[1]
RECORDED = ROOT / "src" / "cap" / "providers" / "recorded"


def png(img: Image.Image) -> bytes:
    buf = io.BytesIO()
    img.save(buf, "PNG")
    return buf.getvalue()


def decode(data: bytes) -> np.ndarray:
    return np.asarray(Image.open(io.BytesIO(data)).convert("RGB"))


def cold_brew_prompt() -> str:
    brief, folder = load_brief(ROOT / "briefs" / "summer-refresh.yaml")
    brand = load_brand(folder / brief.campaign.brand)
    return hero_prompt(brief, next(p for p in brief.products if p.id == "cold-brew-tonic"), brand)


def test_the_recorded_index_matches_the_files_on_disk():
    idx = recorded().index
    assert idx["heroes"] and idx["expansions"]
    for key, name in idx["heroes"].items():
        img = Image.open(RECORDED / "heroes" / name)
        assert img.mode == "RGB" and img.size == (1024, 1024) and name == f"{key}.png"  # lossless, hero-sized
    for key, name in idx["expansions"].items():
        w, h = (int(x) for x in key.split("-")[1].split("x"))
        assert Image.open(RECORDED / "expansions" / name).size == (w, h)  # the file is the size its name claims
    on_disk = {p.name for p in (RECORDED / "expansions").iterdir()}
    assert on_disk == set(idx["expansions"].values()), "every recorded outpaint is indexed, and vice versa"


def test_a_known_prompt_replays_the_real_hero_and_an_unknown_one_gets_a_placeholder():
    mock = MockProvider()
    prompt = cold_brew_prompt()
    real = Image.open(RECORDED / "heroes" / recorded().index["heroes"][prompt_id(prompt)]).convert("RGB")
    assert np.array_equal(decode(mock.generate(prompt, (1024, 1024))), np.asarray(real))  # pixel-identical replay

    other = decode(mock.generate("a prompt nobody recorded", (1024, 1024)))
    assert other.shape == (1024, 1024, 3) and not np.array_equal(other, np.asarray(real))
    assert mock.calls == 2  # replays still count as provider calls, so cost and cache stats behave the same


def test_a_known_image_replays_its_real_outpaint_and_an_unknown_one_is_faked():
    mock = MockProvider()
    yuzu = Image.open(ROOT / "assets" / "sparkling-yuzu" / "hero.png").convert("RGB")
    key = f"{pixel_id(yuzu)}-1024x1821"
    assert key in recorded().index["expansions"], "the shipped packshot is the hero its outpaints were recorded for"
    real = Image.open(RECORDED / "expansions" / recorded().index["expansions"][key]).convert("RGB")
    assert np.array_equal(decode(mock.expand(png(yuzu), (1024, 1821), "extend")), np.asarray(real))

    # a re-encoded copy of the same pixels is still recognised (the lookup is by pixels, not file bytes)
    lossless_copy = io.BytesIO()
    yuzu.save(lossless_copy, "PNG", optimize=True)
    assert np.array_equal(decode(mock.expand(lossless_copy.getvalue(), (1024, 1821), "x")), np.asarray(real))

    unknown = Image.new("RGB", (400, 400), (30, 120, 90))
    faked = decode(mock.expand(png(unknown), (400, 711), "extend"))
    assert faked.shape == (711, 400, 3)
    assert (faked[155:555, :] == np.asarray(unknown)).all()  # the original stays put; only the margins are made up


def test_the_offline_demo_uses_real_images_end_to_end(repo, opts):
    """The report a viewer opens after `cap run` shows real generations, not placeholders."""
    opts.only_ratios, opts.only_locales = ["1:1"], ["en-US"]
    Pipeline(opts).run(repo / "briefs/summer-refresh.yaml")
    out = repo / "output" / "summer-refresh-2026"
    cold = np.asarray(Image.open(out / "cold-brew-tonic" / "_source" / "hero.png").convert("RGB"))
    real = Image.open(RECORDED / "heroes" / recorded().index["heroes"][prompt_id(cold_brew_prompt())]).convert("RGB")
    assert np.array_equal(cold, np.asarray(real))  # generated hero = the recorded one
    yuzu = np.asarray(Image.open(out / "sparkling-yuzu" / "_source" / "hero.png").convert("RGB"))
    assert np.array_equal(yuzu, np.asarray(Image.open(repo / "assets" / "sparkling-yuzu" / "hero.png").convert("RGB")))
