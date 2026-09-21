from cap.compliance.legal import check_copy, load_rules


def run(repo, locale, text):
    rules = load_rules(repo / "legal" / "prohibited_words.yaml")
    res = {c.id: c for c in check_copy(rules, locale, {"message": text})}
    return res["legal.prohibited_terms"].status, res["legal.restricted_claims"].status


def test_case_insensitive_and_word_boundaries(repo):
    assert run(repo, "en-US", "Satisfaction GUARANTEED")[0] == "fail"
    assert run(repo, "en-US", "Unguaranteed fun")[0] == "pass"  # no substring false positive
    assert run(repo, "en-US", "Freshly poured")[1] == "pass"  # 'free' must not match 'Freshly'


def test_word_forms_via_pattern(repo):
    assert run(repo, "en-US", "It cures boredom")[0] == "fail"
    assert run(repo, "en-US", "Secure your spot")[0] == "pass"


def test_language_layer_applies_to_regional_locales(repo):
    assert run(repo, "es-MX", "Sabor garantizado")[0] == "fail"
    assert run(repo, "es-MX", "Sabor GARANTIZÁDO")[0] == "fail"  # accent-insensitive
    assert run(repo, "en-US", "Sabor garantizado")[0] == "pass"  # Spanish rule not applied to en


def test_restricted_claims_warn(repo):
    assert run(repo, "en-US", "The best summer drink") == ("pass", "warn")
    assert run(repo, "en-US", "Our #1 flavor") == ("pass", "warn")


def test_missing_rules_file_is_a_noop(tmp_path):
    rules = load_rules(tmp_path / "nope.yaml")
    assert all(c.status == "pass" for c in check_copy(rules, "en-US", {"message": "guaranteed"}))


def test_regex_escapes_are_not_case_folded(tmp_path):
    r"""Folding the whole pattern turned `\W` into `\w`: "risk-free" passed and "riskxxfree" failed."""
    rules_file = tmp_path / "rules.yaml"
    rules_file.write_text(
        "version: t\nglobal:\n  prohibited:\n    - { pattern: 'Risk\\W+free', label: rf }\n", encoding="utf-8"
    )
    rules = load_rules(rules_file)

    def status(text):
        return check_copy(rules, "en-US", {"message": text})[0].status

    assert status("100% risk-free trial") == "fail"
    assert status("RISK FREE trial") == "fail"  # literal parts still fold case
    assert status("riskxxfree") == "pass"  # \W must not match letters
    assert "\W+" in rules.layers["global"][0].regex.pattern
