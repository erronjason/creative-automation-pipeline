"""The schemas are the documentation: a field without a description is a field nobody can use."""

import json
from pathlib import Path
from typing import get_args, get_origin

import pytest
import yaml
from pydantic import BaseModel
from typer.testing import CliRunner

from cap.brand import Brand, load_brand
from cap.brief import Brief, load_brief
from cap.cli import app


def _fields(model):
    schema = model.model_json_schema()
    for owner, body in [(model.__name__, schema), *schema.get("$defs", {}).items()]:
        for name, prop in body.get("properties", {}).items():
            yield f"{owner}.{name}", prop


@pytest.mark.parametrize("model", [Brief, Brand])
def test_every_field_has_a_description(model):
    undocumented = [path for path, prop in _fields(model) if not prop.get("description", "").strip()]
    assert not undocumented, f"add Field(description=...) to: {undocumented}"


@pytest.mark.parametrize("which", ["brief", "brand"])
def test_cap_schema_prints_valid_json_schema(which):
    res = CliRunner().invoke(app, ["schema", which])
    assert res.exit_code == 0
    schema = json.loads(res.output)
    assert schema["type"] == "object" and schema["properties"]
    assert "root" not in schema["properties"]  # internal, not part of the file format


def test_cap_schema_rejects_unknown_target():
    assert CliRunner().invoke(app, ["schema", "nope"]).exit_code != 0


# --- the annotated reference brief must stay valid and complete -----------------------------------
ROOT = Path(__file__).resolve().parents[1]
REFERENCE = ROOT / "docs" / "brief-reference.yaml"


def _unwrap(annotation):
    """(model, container) inside Model, Model | None, list[Model] or dict[str, Model]; (None, None) otherwise."""
    if isinstance(annotation, type) and issubclass(annotation, BaseModel):
        return annotation, None
    origin = get_origin(annotation)
    for arg in reversed(get_args(annotation)):
        model, container = _unwrap(arg)
        if model is not None:
            return model, (origin if origin in (list, dict) else container)
    return None, None


def _union(items):
    """Merge mappings so a field used by any item counts as used."""
    merged: dict = {}
    for item in items:
        for key, value in item.items():
            if isinstance(value, dict) and isinstance(merged.get(key), dict):
                merged[key] = _union([merged[key], value])
            else:
                merged.setdefault(key, value)
    return merged


def _missing(model, data, path=""):
    missing = []
    for name, field in model.model_fields.items():
        if name not in data:
            missing.append(path + name)
            continue
        inner, container = _unwrap(field.annotation)
        if inner is None:
            continue
        value = data[name]
        if container in (list, dict):  # an example only has to use each field somewhere, not in every item
            value = _union(value if container is list else list(value.values()))
        missing += _missing(inner, value, f"{path}{name}.")
    return missing


def test_reference_brief_is_valid():
    brief, folder = load_brief(REFERENCE)
    load_brand(folder / brief.campaign.brand)  # the brand path in the example resolves, too
    assert len(brief.products) >= 2 and brief.localized_copy


def test_reference_brief_uses_every_field():
    data = yaml.safe_load(REFERENCE.read_text(encoding="utf-8"))
    assert not _missing(Brief, data), "add these to docs/brief-reference.yaml with a comment"


def test_coverage_check_notices_a_missing_field():
    data = yaml.safe_load(REFERENCE.read_text(encoding="utf-8"))
    del data["products"][0]["assets"]["focus"]
    assert _missing(Brief, data) == ["products.assets.focus"]
