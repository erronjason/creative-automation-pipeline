"""Copy localization.

Precedence per locale (most to least trusted):
  1. approved copy in the brief (human transcreation, legal-reviewed)   -> status pass
  2. machine translation with brand-voice guidance                       -> status warn: needs regional review
  3. source-language fallback                                            -> status warn: not localized

Machine output is never silently treated as approved. That keeps regional reviewers in the loop
exactly where their judgment matters and nowhere else.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from typing import Protocol

import httpx

from .brief import Brief


@dataclass
class LocalizedCopy:
    locale: str
    message: str
    cta: str | None
    disclaimer: str | None
    source: str  # brief | machine | fallback
    note: str = ""


class Translator(Protocol):
    name: str

    def translate(
        self, fields: dict[str, str], source: str, target: str, voice: str, context: str
    ) -> dict[str, str]: ...


class OpenAITranslator:
    """Chat-completions transcreation. Subclasses only change the endpoint, key and model."""

    name = "openai"
    url = "https://api.openai.com/v1/chat/completions"

    def __init__(self, client: httpx.Client | None = None):
        self.key = os.getenv("OPENAI_API_KEY", "")
        self.model = os.getenv("OPENAI_TEXT_MODEL", "gpt-5.6-luna")
        self.http = client or httpx.Client(timeout=60.0)

    def translate(self, fields, source, target, voice, context):
        system = (
            "You are a senior advertising transcreator. Adapt ad copy for the target locale: natural, "
            "idiomatic, culturally appropriate, same intent and similar length (headlines must stay short). "
            "Never add claims, prices, or promises that are not in the source. "
            f"Brand voice: {voice}\nCampaign context: {context}\n"
            "Return only a JSON object with exactly the same keys as the input."
        )
        body = {
            "model": self.model,
            "response_format": {"type": "json_object"},
            "messages": [
                {"role": "system", "content": system},
                {
                    "role": "user",
                    "content": json.dumps({"source_locale": source, "target_locale": target, "copy": fields}),
                },
            ],
        }
        r = self.http.post(
            self.url,
            json=body,
            headers={"Authorization": f"Bearer {self.key}"},
        )
        r.raise_for_status()
        out = json.loads(r.json()["choices"][0]["message"]["content"])
        out = out.get("copy", out)
        missing = set(fields) - set(out)
        if missing:
            raise ValueError(f"translator dropped fields {missing}")
        return {k: str(out[k]).strip() for k in fields}


class OpenRouterTranslator(OpenAITranslator):
    """Same request shape through OpenRouter, so one OPENROUTER_API_KEY covers images and copy."""

    name = "openrouter"
    url = "https://openrouter.ai/api/v1/chat/completions"

    def __init__(self, client: httpx.Client | None = None):
        super().__init__(client)
        self.key = os.getenv("OPENROUTER_API_KEY", "")
        self.model = os.getenv("OPENROUTER_TEXT_MODEL", "openai/gpt-5.6-luna")


TRANSLATORS: dict[str, tuple[str, type[OpenAITranslator]]] = {
    "openai": ("OPENAI_API_KEY", OpenAITranslator),
    "openrouter": ("OPENROUTER_API_KEY", OpenRouterTranslator),
}


def get_translator(mode: str) -> Translator | None:
    if mode == "none":
        return None
    if mode == "auto":  # first configured backend wins, in the order above
        for env, cls in TRANSLATORS.values():
            if os.getenv(env):
                return cls()
        return None
    if mode in TRANSLATORS:
        env, cls = TRANSLATORS[mode]
        if not os.getenv(env):
            raise RuntimeError(f"--translator {mode} requires {env}")
        return cls()
    return None


def resolve_copy(brief: Brief, voice: str, translator: Translator | None, emit=None) -> dict[str, LocalizedCopy]:
    c = brief.campaign
    src = {"message": c.message, "cta": c.cta, "disclaimer": c.disclaimer}
    out: dict[str, LocalizedCopy] = {}
    for loc in brief.locales:
        approved = brief.localized_copy.get(loc)
        if loc == c.source_locale and not approved:
            out[loc] = LocalizedCopy(loc, c.message, c.cta, c.disclaimer, "brief", "source copy")
            continue
        if approved:
            out[loc] = LocalizedCopy(
                loc,
                approved.message,
                approved.cta if approved.cta is not None else c.cta,
                approved.disclaimer if approved.disclaimer is not None else c.disclaimer,
                "brief",
                "approved localized copy from brief",
            )
            continue
        fields = {k: v for k, v in src.items() if v}
        if translator:
            try:
                ctx = f"{c.name}; audience: {brief.target.audience}; region: {brief.target.region}"
                t = translator.translate(fields, c.source_locale, loc, voice, ctx)
                out[loc] = LocalizedCopy(
                    loc,
                    t["message"],
                    t.get("cta"),
                    t.get("disclaimer"),
                    "machine",
                    f"machine-translated by {translator.name}; needs regional review",
                )
                if emit:
                    emit("localize", f"{loc}: machine-translated", "warn")
                continue
            except Exception as e:  # translation failure must not sink the whole run
                if emit:
                    emit("localize", f"{loc}: translation failed ({e}); using source copy", "warn")
        out[loc] = LocalizedCopy(
            loc,
            c.message,
            c.cta,
            c.disclaimer,
            "fallback",
            f"no approved {loc} copy and no translator configured; source-language copy used",
        )
        if emit:
            emit("localize", f"{loc}: no approved copy or translator; falling back to {c.source_locale}", "warn")
    return out
