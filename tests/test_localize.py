import json

import httpx
import pytest

from cap.brief import load_brief
from cap.localize import ChatTranslator, get_translator, resolve_copy


class Boom:
    name = "boom"

    def translate(self, *a):
        raise RuntimeError("upstream timeout")


def test_precedence_approved_then_machine_then_fallback(repo, monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    sent = {}

    def handler(req):
        body = json.loads(req.content)
        sent["model"] = body["model"]
        sent["user"] = json.loads(body["messages"][1]["content"])
        out = {"message": "L'été, fraîchement servi.", "cta": "Trouvez-le près de chez vous"}
        return httpx.Response(200, json={"choices": [{"message": {"content": json.dumps(out)}}]})

    brief, _ = load_brief(repo / "briefs/summer-refresh.yaml")
    t = ChatTranslator("openai", client=httpx.Client(transport=httpx.MockTransport(handler)))
    copies = resolve_copy(brief, "sunny", t)
    assert copies["en-US"].source == "brief"
    assert copies["es-MX"].source == "brief" and copies["es-MX"].message == "El verano, recién servido."
    assert copies["fr-CA"].source == "machine" and copies["fr-CA"].message.startswith("L'été")
    assert sent["user"]["target_locale"] == "fr-CA"  # only the untranslated locale hit the API

    events = []
    fb = resolve_copy(brief, "sunny", Boom(), lambda *a: events.append(a))
    assert fb["fr-CA"].source == "fallback" and fb["fr-CA"].message == brief.campaign.message
    assert any("translation failed" in e[1] for e in events)


def test_translator_selection_and_openrouter_endpoint(monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    assert get_translator("auto") is None and get_translator("none") is None
    with pytest.raises(RuntimeError, match="OPENROUTER_API_KEY"):
        get_translator("openrouter")
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-test")
    assert get_translator("auto").name == "openrouter"
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    assert get_translator("auto").name == "openai"  # OpenAI first when both are set

    seen = {}

    def handler(req):
        seen["url"], seen["auth"] = str(req.url), req.headers["authorization"]
        return httpx.Response(200, json={"choices": [{"message": {"content": '{"message": "Bonjour"}'}}]})

    t = ChatTranslator("openrouter", client=httpx.Client(transport=httpx.MockTransport(handler)))
    assert t.translate({"message": "Hello"}, "en-US", "fr-CA", "warm", "ctx") == {"message": "Bonjour"}
    assert seen["url"] == "https://openrouter.ai/api/v1/chat/completions" and seen["auth"] == "Bearer sk-or-test"
