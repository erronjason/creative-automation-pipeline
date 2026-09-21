import time
from dataclasses import asdict

from fastapi.testclient import TestClient

from cap.pipeline import RunOptions
from cap.server import create_app


def client(repo, opts) -> TestClient:
    app = create_app(RunOptions(**asdict(opts)), str(repo / "briefs"))
    return TestClient(app, base_url="http://127.0.0.1:8765")


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


def test_refuses_cross_origin_writes_and_foreign_hosts(repo, opts):
    c = client(repo, opts)
    png = b"\x89PNG\r\n\x1a\n"  # content is irrelevant: the request must be refused before it is read
    evil = {"Origin": "http://evil.example"}
    assert c.post("/api/assets/victim", files={"file": ("x.png", png)}, headers=evil).status_code == 403
    assert c.post("/api/runs", json={"file": "summer-refresh.yaml"}, headers=evil).status_code == 403
    assert not (repo / "assets/victim").exists()
    # same-origin browser requests and origin-less clients (curl, tests) still work
    same = {"Origin": "http://127.0.0.1:8765"}
    assert c.post("/api/validate", json={"file": "summer-refresh.yaml"}, headers=same).status_code == 200
    assert c.get("/api/state", headers=evil).status_code == 200  # reads are not state-changing
    # DNS rebinding: an attacker's hostname pointing at 127.0.0.1
    assert c.get("/api/state", headers={"Host": "attacker.example:8765"}).status_code == 400


def test_edited_scratch_briefs_are_not_listed(repo, opts):
    (repo / "briefs/summer-refresh.edited.yaml").write_text(
        (repo / "briefs/summer-refresh.yaml").read_text(encoding="utf-8"), encoding="utf-8"
    )
    files = {b["file"] for b in client(repo, opts).get("/api/state").json()["briefs"]}
    assert "summer-refresh.yaml" in files and "summer-refresh.edited.yaml" not in files
