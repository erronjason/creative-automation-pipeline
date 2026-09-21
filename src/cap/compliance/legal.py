"""Legal copy checks: prohibited terms (fail) and restricted claims (warn, needs substantiation).

Rules are data (legal/prohibited_words.yaml), layered global -> language -> locale, so legal
teams can maintain market-specific lists without touching code. Matching is case- and
accent-insensitive with word boundaries, so 'guaranteed' matches 'GUARANTEED' but not
'unguaranteed', and 'garantizado' matches 'garantizádo'.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path

import yaml

from ..manifest import CheckResult


def fold(s: str) -> str:
    s = unicodedata.normalize("NFKD", s)
    return "".join(ch for ch in s if not unicodedata.combining(ch)).casefold()


@dataclass
class Rule:
    label: str
    regex: re.Pattern
    severity: str  # fail | warn
    reason: str


@dataclass
class LegalRules:
    version: str = "none"
    layers: dict[str, list[Rule]] = field(default_factory=dict)

    def for_locale(self, locale: str) -> list[Rule]:
        lang = locale.split("-")[0]
        return (
            self.layers.get("global", [])
            + self.layers.get(lang, [])
            + (self.layers.get(locale, []) if locale != lang else [])
        )


def _compile(entry: dict, severity: str) -> Rule:
    if "pattern" in entry:
        body = fold(entry["pattern"])
        label = entry.get("label", entry["pattern"])
    else:
        body = re.escape(fold(entry["term"]))
        label = entry["term"]
    return Rule(label, re.compile(rf"(?<!\w){body}(?!\w)"), severity, entry.get("reason", ""))


def load_rules(path: str | Path | None) -> LegalRules:
    if not path or not Path(path).exists():
        return LegalRules()
    data = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
    rules = LegalRules(version=str(data.get("version", "unversioned")))
    scopes = {"global": data.get("global", {}), **(data.get("locales") or {})}
    for scope, body in scopes.items():
        body = body or {}
        rules.layers[scope] = [_compile(e, "fail") for e in body.get("prohibited", [])] + [
            _compile(e, "warn") for e in body.get("restricted", [])
        ]
    return rules


def check_copy(rules: LegalRules, locale: str, texts: dict[str, str | None]) -> list[CheckResult]:
    hits: list[tuple[Rule, str]] = []
    for name, text in texts.items():
        if not text:
            continue
        folded = fold(text)
        hits += [(r, name) for r in rules.for_locale(locale) if r.regex.search(folded)]
    fails = [(r, n) for r, n in hits if r.severity == "fail"]
    warns = [(r, n) for r, n in hits if r.severity == "warn"]

    def describe(items):
        return "; ".join(f"'{r.label}' in {n}" + (f" ({r.reason})" if r.reason else "") for r, n in items)

    return [
        CheckResult(
            id="legal.prohibited_terms",
            category="legal",
            status="fail" if fails else "pass",
            detail=describe(fails) if fails else f"no prohibited terms (rules {rules.version})",
            value=len(fails),
        ),
        CheckResult(
            id="legal.restricted_claims",
            category="legal",
            status="warn" if warns else "pass",
            detail=("needs substantiation: " + describe(warns)) if warns else "no restricted claims",
            value=len(warns),
        ),
    ]
