from typer.testing import CliRunner

from cap.cli import app


def test_missing_translator_key_is_a_message_not_a_traceback(repo, monkeypatch):
    monkeypatch.chdir(repo)
    monkeypatch.setenv("OPENROUTER_API_KEY", "")
    res = CliRunner().invoke(app, ["run", "briefs/summer-refresh.yaml", "--translator", "openrouter", "--ratio", "1:1"])
    assert res.exit_code == 1
    assert "OPENROUTER_API_KEY" in res.output and "Traceback" not in res.output
