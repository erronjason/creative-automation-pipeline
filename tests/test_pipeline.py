import csv
import json

from cap.events import EventLog
from cap.manifest import Manifest, Review
from cap.pipeline import Pipeline
from cap.providers.mock import MockProvider


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


def _approve(repo, product):
    path = repo / "output/summer-refresh-2026/manifest.json"
    m = Manifest.model_validate_json(path.read_text(encoding="utf-8"))
    for v in m.variants:
        if v.product_id == product:
            v.review = Review(state="approved", by="t")
    path.write_text(m.model_dump_json(), encoding="utf-8")


def test_partial_rerun_keeps_the_rest_of_the_campaign_and_its_approvals(repo, opts):
    opts.only_ratios = ["1:1"]
    Pipeline(opts).run(repo / "briefs/summer-refresh.yaml")
    _approve(repo, "sparkling-yuzu")

    opts.only_products = ["cold-brew-tonic"]  # regenerate one product only
    seen = []
    m = Pipeline(opts, EventLog([seen.append])).run(repo / "briefs/summer-refresh.yaml")
    done = next(e.message for e in seen if e.stage == "done")
    assert done.startswith("3 regenerated, 3 kept:"), done  # not "6 variants", which would claim work not done
    assert m.stats.variants == 6, "the other product's variants must survive in the manifest"
    assert {v.product_id for v in m.variants} == {"sparkling-yuzu", "cold-brew-tonic"}
    assert sum(v.review.state == "approved" for v in m.variants) == 3  # approvals not discarded
    assert {p.id for p in m.products} == {"sparkling-yuzu", "cold-brew-tonic"}
    assert m.aspect_ratios == ["1:1", "9:16", "16:9"]  # the manifest describes the campaign, not the slice


def test_partial_rerun_does_not_mix_generations(repo, opts):
    opts.only_ratios = ["1:1"]
    Pipeline(opts).run(repo / "briefs/summer-refresh.yaml")
    path = repo / "briefs/summer-refresh.yaml"
    path.write_text(path.read_text(encoding="utf-8") + "\n# edited\n", encoding="utf-8")  # brief bytes changed

    opts.only_products = ["cold-brew-tonic"]
    seen = []
    m = Pipeline(opts, EventLog([seen.append])).run(path)
    assert {v.product_id for v in m.variants} == {"cold-brew-tonic"}
    assert any("not carried over" in e.message for e in seen)  # said out loud, not silently dropped


class _Tagged(MockProvider):
    def __init__(self, tag):
        super().__init__()
        self.gen_tag = tag


def test_generation_cache_key_includes_provider_settings(repo, opts):
    opts.only_products, opts.only_ratios, opts.only_locales = ["cold-brew-tonic"], ["1:1"], ["en-US"]
    brief = repo / "briefs/summer-refresh.yaml"
    Pipeline(opts, provider=_Tagged("quality=low")).run(brief)
    same = Pipeline(opts, provider=_Tagged("quality=low"))
    same.run(brief)
    other = Pipeline(opts, provider=_Tagged("quality=high"))
    other.run(brief)
    assert same.provider.calls == 0  # identical settings: cache hit
    assert other.provider.calls == 1  # changed quality must not serve the old image
