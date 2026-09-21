import base64
import io
import json

import httpx
import pytest
from PIL import Image

from cap.providers import ProviderError, base
from cap.providers.firefly import FireflyProvider
from cap.providers.openai import OpenAIProvider
from cap.providers.openrouter import OpenRouterProvider, nearest_ratio


def png(size=(64, 64), color=(200, 100, 50)) -> bytes:
    b = io.BytesIO()
    Image.new("RGB", size, color).save(b, "PNG")
    return b.getvalue()


@pytest.fixture(autouse=True)
def fast_retries(monkeypatch):
    monkeypatch.setattr(base.time, "sleep", lambda s: None)


def test_openai_generate_request_and_decode(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    seen = {}

    def handler(req: httpx.Request):
        seen["url"], seen["auth"], seen["body"] = str(req.url), req.headers["authorization"], json.loads(req.content)
        return httpx.Response(200, json={"data": [{"b64_json": base64.b64encode(png()).decode()}]})

    p = OpenAIProvider(client=httpx.Client(transport=httpx.MockTransport(handler)))
    out = p.generate("a can of yuzu soda", (1024, 1024))
    assert Image.open(io.BytesIO(out)).size == (64, 64)
    assert seen["url"].endswith("/v1/images/generations") and seen["auth"] == "Bearer sk-test"
    assert seen["body"]["size"] == "1024x1024" and seen["body"]["model"] == "gpt-image-2"
    assert p.calls == 1


def test_openai_expand_sends_image_and_mask_multipart(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    seen = {}

    def handler(req: httpx.Request):
        seen["ctype"], seen["body"] = req.headers["content-type"], req.content
        return httpx.Response(200, json={"data": [{"b64_json": base64.b64encode(png((1824, 1024))).decode()}]})

    p = OpenAIProvider(client=httpx.Client(transport=httpx.MockTransport(handler)))
    p.expand(png((1024, 1024)), (1821, 1024), "extend")
    assert seen["ctype"].startswith("multipart/form-data")
    assert b'name="mask"' in seen["body"] and b'name="image"' in seen["body"]
    assert b"1824x1024" in seen["body"]  # rounded up to a multiple of 16, never cropping the source


def test_retries_transient_errors_then_succeeds(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    calls = {"n": 0}

    def handler(req):
        calls["n"] += 1
        if calls["n"] < 3:
            return httpx.Response(429, json={"error": "rate limited"})
        return httpx.Response(200, json={"data": [{"b64_json": base64.b64encode(png()).decode()}]})

    OpenAIProvider(client=httpx.Client(transport=httpx.MockTransport(handler))).generate("x", (1024, 1024))
    assert calls["n"] == 3


def test_client_errors_fail_fast_with_message(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    calls = {"n": 0}

    def handler(req):
        calls["n"] += 1
        return httpx.Response(400, json={"error": {"message": "content policy"}})

    p = OpenAIProvider(client=httpx.Client(transport=httpx.MockTransport(handler)))
    with pytest.raises(ProviderError, match="HTTP 400.*content policy"):
        p.generate("x", (1024, 1024))
    assert calls["n"] == 1


def test_unconfigured_providers_say_what_is_missing():
    assert not OpenAIProvider().status().ready
    st = FireflyProvider().status()
    assert not st.ready and "FIREFLY_CLIENT_ID" in st.detail


def test_firefly_async_generate_flow(monkeypatch):
    monkeypatch.setenv("FIREFLY_CLIENT_ID", "cid")
    monkeypatch.setenv("FIREFLY_CLIENT_SECRET", "secret")
    log = []
    polls = {"n": 0}

    def handler(req: httpx.Request):
        u = str(req.url)
        log.append((req.method, u.split("?")[0]))
        if "ims/token" in u:
            assert b"client_credentials" in req.content and b"firefly_api" in req.content
            return httpx.Response(200, json={"access_token": "tok", "expires_in": 86400})
        if u.endswith("/v3/images/generate-async"):
            assert req.headers["x-api-key"] == "cid" and req.headers["authorization"] == "Bearer tok"
            body = json.loads(req.content)
            assert body["size"] == {"width": 2688, "height": 1536}
            return httpx.Response(202, json={"jobId": "j1", "statusUrl": "https://firefly-api.adobe.io/v3/status/j1"})
        if "/v3/status/" in u:
            polls["n"] += 1
            if polls["n"] == 1:
                return httpx.Response(200, json={"status": "running"})
            return httpx.Response(
                200,
                json={
                    "status": "succeeded",
                    "result": {"outputs": [{"image": {"url": "https://cdn.example/img.png"}}]},
                },
            )
        if u.startswith("https://cdn.example"):
            return httpx.Response(200, content=png((2688, 1536)))
        return httpx.Response(404)

    p = FireflyProvider(client=httpx.Client(transport=httpx.MockTransport(handler)), poll_interval=0)
    p._limiter.interval = 0
    out = p.generate("yuzu soda", (1920, 1080))
    assert Image.open(io.BytesIO(out)).size == (2688, 1536)
    assert [m for m, _ in log] == ["POST", "POST", "GET", "GET", "GET"]


def test_rate_limiter_spaces_calls(monkeypatch):
    clock = {"t": 100.0}
    slept = []
    monkeypatch.setattr(base.time, "monotonic", lambda: clock["t"])
    monkeypatch.setattr(base.time, "sleep", lambda s: slept.append(s))
    rl = base.RateLimiter(per_minute=4)
    for _ in range(3):
        rl.wait()
    assert slept == [15.0, 30.0]


def openrouter(monkeypatch, handler, **env):
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-test")
    for k, v in env.items():
        monkeypatch.setenv(k, v)
    return OpenRouterProvider(client=httpx.Client(transport=httpx.MockTransport(handler)))


def test_openrouter_generate_request_cost_and_decode(monkeypatch):
    seen = {}

    def handler(req: httpx.Request):
        seen["url"], seen["auth"], seen["body"] = str(req.url), req.headers["authorization"], json.loads(req.content)
        return httpx.Response(
            200, json={"data": [{"b64_json": base64.b64encode(png((1024, 1024))).decode()}], "usage": {"cost": 0.04}}
        )

    p = openrouter(monkeypatch, handler)
    out = p.generate("a can of yuzu soda", (1024, 1024))
    assert Image.open(io.BytesIO(out)).size == (1024, 1024)
    assert seen["url"] == "https://openrouter.ai/api/v1/images" and seen["auth"] == "Bearer sk-or-test"
    assert seen["body"]["model"] == "openai/gpt-image-2" and seen["body"]["aspect_ratio"] == "1:1"
    assert "quality" not in seen["body"]  # not every model supports it; only sent when configured
    assert p.calls == 1 and p.actual_cost_usd() == 0.04


def test_openrouter_quality_is_opt_in_and_cost_absent_means_estimate(monkeypatch):
    seen = {}

    def handler(req):
        seen["body"] = json.loads(req.content)
        return httpx.Response(200, json={"data": [{"b64_json": base64.b64encode(png((64, 64))).decode()}]})

    p = openrouter(monkeypatch, handler, OPENROUTER_IMAGE_QUALITY="medium", OPENROUTER_IMAGE_MODEL="x/y")
    p.generate("x", (1024, 1024))
    assert seen["body"]["quality"] == "medium" and seen["body"]["model"] == "x/y"
    assert p.actual_cost_usd() is None  # pipeline falls back to calls x est_cost_per_image


def test_openrouter_expand_sends_blurred_layout_and_matches_canvas(monkeypatch):
    seen = {}

    def handler(req: httpx.Request):
        seen["body"] = json.loads(req.content)
        # the model answers at its nearest supported ratio (16:9), not the exact canvas
        return httpx.Response(200, json={"data": [{"b64_json": base64.b64encode(png((1920, 1080))).decode()}]})

    p = openrouter(monkeypatch, handler)
    out = p.expand(png((1024, 1024)), (1821, 1024), "extend")
    assert Image.open(io.BytesIO(out)).size == (1821, 1024)  # cover-cropped, never stretched
    body = seen["body"]
    assert body["aspect_ratio"] == "16:9" and "extend" in body["prompt"]
    assert "blurred placeholder margins" in body["prompt"]
    ref = body["input_references"][0]["image_url"]["url"]
    assert ref.startswith("data:image/png;base64,")
    layout = Image.open(io.BytesIO(base64.b64decode(ref.split(",", 1)[1])))
    assert layout.size == (1821, 1024)
    assert layout.getpixel((2, 2))[3] == 255  # opaque placeholder margins, not a transparent hole
    assert layout.getpixel((910, 512))[3] == 255  # source centred


def test_openrouter_nearest_ratio():
    assert nearest_ratio((1080, 1920)) == "9:16"
    assert nearest_ratio((1920, 1080)) == "16:9"
    assert nearest_ratio((1080, 1350)) == "3:4"  # 4:5 is not in the shared enum


def test_openrouter_error_inside_200_and_client_errors(monkeypatch):
    p = openrouter(monkeypatch, lambda req: httpx.Response(200, json={"error": {"message": "upstream down"}}))
    with pytest.raises(ProviderError, match="upstream error"):
        p.generate("x", (1024, 1024))
    p = openrouter(monkeypatch, lambda req: httpx.Response(402, json={"error": {"message": "insufficient credits"}}))
    with pytest.raises(ProviderError, match="HTTP 402.*insufficient credits"):
        p.generate("x", (1024, 1024))


def test_openrouter_unconfigured_status(monkeypatch):
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    st = OpenRouterProvider().status()
    assert not st.ready and "OPENROUTER_API_KEY" in st.detail


def test_firefly_rate_limit_is_read_when_the_provider_is_built(monkeypatch):
    """FIREFLY_RPM lives in .env, which is loaded after this module is imported."""
    monkeypatch.setenv("FIREFLY_RPM", "60")
    assert FireflyProvider()._limiter.interval == 1.0
    monkeypatch.delenv("FIREFLY_RPM")
    assert FireflyProvider()._limiter.interval == 15.0  # documented default: 4 requests/minute


def test_quality_changes_the_cache_tags(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "k")
    monkeypatch.setenv("OPENAI_IMAGE_QUALITY", "high")
    assert OpenAIProvider().gen_tag == "quality=high"
    monkeypatch.setenv("OPENROUTER_API_KEY", "k")
    monkeypatch.delenv("OPENROUTER_IMAGE_QUALITY", raising=False)
    assert OpenRouterProvider().gen_tag == ""  # unset leaves existing cache keys valid
    monkeypatch.setenv("OPENROUTER_IMAGE_QUALITY", "low")
    p = OpenRouterProvider()
    assert p.gen_tag == "quality=low" and "quality=low" in p.cache_tag and "layout=blur" in p.cache_tag
