import json

import pytest
from pydantic import ValidationError

from cap.brief import load_brief, parse_brief

BASE = {
    "campaign": {"id": "c-1", "name": "C", "brand": "b.yaml", "message": "Hello"},
    "target": {"region": "NA", "audience": "adults", "markets": [{"code": "US", "locale": "en-US"}]},
    "products": [
        {"id": "a", "name": "A", "description": "thing a"},
        {"id": "b", "name": "B", "description": "thing b"},
    ],
}


def brief(**over):
    d = json.loads(json.dumps(BASE))
    for k, v in over.items():
        d[k] = v
    return d


def test_example_briefs_are_valid(repo):
    for f in (repo / "briefs").glob("*.yaml"):
        b, _ = load_brief(f)
        assert len(b.products) >= 2


def test_json_is_accepted():
    b = parse_brief(json.dumps(BASE))
    assert b.aspect_ratios == ["1:1", "9:16", "16:9"]  # sensible default
    assert b.locales == ["en-US"]


def test_requires_two_products():
    with pytest.raises(ValidationError, match="at least 2"):
        parse_brief(json.dumps(brief(products=BASE["products"][:1])))


def test_rejects_unknown_ratio():
    with pytest.raises(ValidationError, match="unsupported aspect ratio"):
        parse_brief(json.dumps(brief(aspect_ratios=["1:1", "3:7"])))


def test_rejects_copy_for_untargeted_locale():
    with pytest.raises(ValidationError, match="not targeted"):
        parse_brief(json.dumps(brief(localized_copy={"ja-JP": {"message": "x"}})))


def test_rejects_duplicate_product_ids_and_bad_slugs():
    dup = [BASE["products"][0], BASE["products"][0]]
    with pytest.raises(ValidationError, match="duplicate"):
        parse_brief(json.dumps(brief(products=dup)))
    bad = [{"id": "Bad Id", "name": "x", "description": "xyz"}, BASE["products"][1]]
    with pytest.raises(ValidationError, match="slug"):
        parse_brief(json.dumps(brief(products=bad)))


def test_unknown_fields_are_errors_not_silently_ignored():
    d = brief()
    d["campaign"]["mesage"] = "typo"
    with pytest.raises(ValidationError, match="mesage"):
        parse_brief(json.dumps(d))


def test_locale_accepts_language_script_region_forms():
    from cap.brief import Market

    for ok in ("en-US", "pt-BR", "fr", "es-419", "zh-Hans-CN", "sr-Latn"):
        assert Market(code="X", locale=ok).locale == ok
    for bad in ("EN-us", "english", "en_US", "e", "en-USA"):
        with pytest.raises(ValidationError):
            Market(code="X", locale=bad)
