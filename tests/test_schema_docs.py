"""The schemas are the documentation: a field without a description is a field nobody can use."""

import json

import pytest
from typer.testing import CliRunner

from cap.brand import Brand
from cap.brief import Brief
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
