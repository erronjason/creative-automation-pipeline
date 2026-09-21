import csv
import json

from cap.events import EventLog
from cap.manifest import Manifest, Review
from cap.pipeline import Pipeline


def test_end_to_end_outputs_and_manifest(repo, opts):
    m = Pipeline(opts).run(repo / "briefs/summer-refresh.yaml")
    out = repo / "output" / "summer-refresh-2026"

    # 2 products x 3 ratios x 3 locales, organized by product and aspect ratio
    assert m.stats.variants == 18
    for p in ("sparkling-yuzu", "cold-brew-tonic"):
        for r in ("1x1", "9x16", "16x9"):
            for loc in ("en-US", "es-MX", "fr-CA"):
                assert (out / p / r / f"{loc}.png").is_file()

    sizes = {v.ratio: (v.width, v.height) for v in m.variants}
    assert sizes == {"1:1": (1080, 1080), "9:16": (1080, 1920), "16:9": (1920, 1080)}

    src = {p.id: p.asset_source for p in m.products}
    assert src == {"sparkling-yuzu": "reused", "cold-brew-tonic": "generated"}

    # fr-CA has no approved copy and no translator in tests: flagged, never silently shipped
    fr = [v for v in m.variants if v.locale == "fr-CA"]
    assert all(v.copy_source == "fallback" and v.status != "pass" for v in fr)
    es = next(v for v in m.variants if v.locale == "es-MX")
    assert es.copy_text["message"] == "El verano, recién servido."

    assert Manifest.model_validate_json((out / "manifest.json").read_text(encoding="utf-8"))
    rows = list(csv.DictReader((out / "variants.csv").open()))
    assert len(rows) == 18 and rows[0]["variant_id"].startswith("summer-refresh-2026/")
    assert "window.__CAP__" in (out / "report.html").read_text(encoding="utf-8")
    assert (out / "events.jsonl").read_text(encoding="utf-8").count("\n") > 10


def test_rerun_hits_cache_and_spends_nothing(repo, opts):
    first = Pipeline(opts).run(repo / "briefs/summer-refresh.yaml")
    assert first.stats.genai_calls > 0
    second = Pipeline(opts).run(repo / "briefs/summer-refresh.yaml")
    assert second.stats.genai_calls == 0
    assert second.stats.cache_hits == first.stats.genai_calls
    assert [v.sha256 for v in first.variants] == [v.sha256 for v in second.variants]  # deterministic


def test_reviews_survive_rerun_only_if_pixels_unchanged(repo, opts):
    m = Pipeline(opts).run(repo / "briefs/summer-refresh.yaml")
    path = repo / "output/summer-refresh-2026/manifest.json"
    m.variants[0].review = Review(state="approved", by="t")
    m.variants[1].review = Review(state="approved", by="t")
    m.variants[1].sha256 = "stale"  # pretend the image changed since approval
    path.write_text(m.model_dump_json(), encoding="utf-8")
    again = Pipeline(opts).run(repo / "briefs/summer-refresh.yaml")
    assert again.variants[0].review.state == "approved"
    assert again.variants[1].review.state == "pending"


def test_legal_failures_are_reported_per_variant(repo, opts):
    m = Pipeline(opts).run(repo / "briefs/compliance-demo.yaml")
    assert m.stats.variants == 16 and m.stats.failed == 16
    v = m.variants[0]
    legal = next(c for c in v.checks if c.id == "legal.prohibited_terms")
    assert legal.status == "fail" and "guarant" in legal.detail.lower()


def test_filters_and_events(repo, opts):
    opts.only_products, opts.only_ratios, opts.only_locales = ["sparkling-yuzu"], ["1:1"], ["en-US"]
    seen = []
    m = Pipeline(opts, EventLog([seen.append])).run(repo / "briefs/summer-refresh.yaml")
    assert m.stats.variants == 1 and m.variants[0].status == "pass"
    assert {e.stage for e in seen} >= {"validate", "assets", "reframe", "variant", "done"}
    assert json.loads(m.model_dump_json())["stats"]["genai_calls"] == 0  # reused asset, square crop
