import time
from dataclasses import asdict

from fastapi.testclient import TestClient

from cap.pipeline import RunOptions
from cap.server import create_app


def client(repo, opts) -> TestClient:
    return TestClient(create_app(RunOptions(**asdict(opts)), str(repo / "briefs")))


def test_state_validate_run_and_review(repo, opts):
    c = client(repo, opts)
    st = c.get("/api/state").json()
    assert {b["file"] for b in st["briefs"]} >= {"summer-refresh.yaml", "compliance-demo.yaml"}
    assert next(p for p in st["providers"] if p["name"] == "mock")["ready"]

    v = c.post("/api/validate", json={"file": "summer-refresh.yaml"}).json()
    assert v["ok"] and "18" in v["summary"]
    assets = {a["product_id"]: a["found"] for a in v["assets"]}
    assert assets == {"sparkling-yuzu": True, "cold-brew-tonic": False}

    bad = c.post("/api/validate", json={"file": "summer-refresh.yaml", "text": "campaign: {}"}).json()
    assert not bad["ok"] and bad["errors"]

    text = (
        (repo / "briefs/summer-refresh.yaml").read_text(encoding="utf-8").replace('["1:1", "9:16", "16:9"]', '["1:1"]')
    )
    run_id = c.post("/api/runs", json={"file": "summer-refresh.yaml", "text": text, "translator": "none"}).json()[
        "run_id"
    ]
    for _ in range(300):
        s = c.get(f"/api/runs/{run_id}").json()
        if s["status"] != "running":
            break
        time.sleep(0.2)
    assert s["status"] == "done", s
    assert (repo / "briefs/summer-refresh.edited.yaml").exists()  # original untouched

    m = c.get("/api/campaigns").json()[0]
    vid = m["variants"][0]["id"]
    r = c.post(f"/api/campaigns/{m['campaign_id']}/review", json={"variant_id": vid, "state": "approved", "note": "ok"})
    assert r.json()["review"]["state"] == "approved"
    assert c.get(f"/files/{m['campaign_id']}/{m['variants'][0]['path']}").status_code == 200


def test_rejects_path_traversal_and_bad_input(repo, opts):
    c = client(repo, opts)
    assert c.get("/api/briefs/..%2F..%2Fetc%2Fpasswd").status_code == 404
    assert c.post("/api/runs", json={"file": "summer-refresh.yaml", "provider": "nope"}).status_code == 400
    assert c.post("/api/assets/Bad Id", files={"file": ("x.png", b"x")}).status_code in (400, 404)
    assert c.post("/api/assets/new-product", files={"file": ("x.png", b"not an image")}).status_code == 400
