"""Self-contained HTML viewer.

One front end, three modes:
  * report   - written next to every run's outputs; opens from disk (file://), images relative
  * showcase - static export for GitHub Pages; no server, no keys
  * live     - served by `cap serve`; adds run controls and approve/reject
"""

from __future__ import annotations

import json
from importlib import resources

from .manifest import Manifest


def _asset(name: str) -> str:
    return resources.files("cap.web").joinpath("static", name).read_text(encoding="utf-8")


def render_report(manifests: list[Manifest], mode: str = "report", image_base: str = "") -> str:
    payload = {
        "mode": mode,
        "imageBase": image_base,
        "manifests": [json.loads(m.model_dump_json()) for m in manifests],
    }
    data = json.dumps(payload, ensure_ascii=False).replace("</", "<\\/")
    html = _asset("index.html")
    return (
        html.replace("/*__CSS__*/", _asset("app.css"))
        .replace("/*__DATA__*/", f"window.__CAP__ = {data};")
        .replace("/*__JS__*/", _asset("app.js"))
    )
