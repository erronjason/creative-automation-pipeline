from typer.testing import CliRunner

from cap.cli import app


def test_missing_translator_key_is_a_message_not_a_traceback(repo, monkeypatch):
    monkeypatch.chdir(repo)
    monkeypatch.setenv("OPENROUTER_API_KEY", "")
    res = CliRunner().invoke(app, ["run", "briefs/summer-refresh.yaml", "--translator", "openrouter", "--ratio", "1:1"])
    assert res.exit_code == 1
    assert "OPENROUTER_API_KEY" in res.output and "Traceback" not in res.output


def test_output_survives_a_non_utf8_stdout(repo):
    """Redirected output on Windows defaults to cp1252, which cannot encode the check marks the CLI prints.

    CI hides this by forcing UTF-8, so run the CLI in a child process with a cp1252 stdout, as
    `cap validate > log.txt` or a script capturing output would.
    """
    import os
    import subprocess
    import sys

    env = {**os.environ, "PYTHONIOENCODING": "cp1252", "PYTHONUTF8": "0"}
    res = subprocess.run(
        [sys.executable, "-c", "from cap.cli import app; app()", "validate", "briefs/summer-refresh.yaml"],
        cwd=repo,
        env=env,
        capture_output=True,
    )
    assert res.returncode == 0, res.stderr.decode("utf-8", "replace")[-400:]
    assert "✓".encode() in res.stdout  # emitted as UTF-8, whatever the platform default was
