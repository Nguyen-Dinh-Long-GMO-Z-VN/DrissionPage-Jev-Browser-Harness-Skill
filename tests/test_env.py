"""Loading Jev's settings from a .env file."""

import os

from jev_ultrafast.env import load_env


def test_loads_only_jev_variables_and_keeps_the_environment(monkeypatch, tmp_path):
    (tmp_path / ".env").write_text(
        "# comment\n"
        "TYPESAFE_API_KEY=ts-key\n"
        'TEXT_MODEL_API_KEY="text-key"\n'
        "export TEXT_MODEL='m'\n"
        "TEXT_MODEL_BASE_URL=https://already.set/v1\n"
        "AWS_SECRET_ACCESS_KEY=unrelated\n"
        "OPENAI_API_KEY=unrelated\n"
    )
    for name in ("TYPESAFE_API_KEY", "TEXT_MODEL_API_KEY", "TEXT_MODEL", "AWS_SECRET_ACCESS_KEY", "OPENAI_API_KEY"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("TEXT_MODEL_BASE_URL", "https://exported/v1")
    monkeypatch.chdir(tmp_path)
    assert load_env() == tmp_path / ".env"
    assert os.environ["TYPESAFE_API_KEY"] == "ts-key"
    assert os.environ["TEXT_MODEL_API_KEY"] == "text-key"
    assert os.environ["TEXT_MODEL"] == "m"
    assert os.environ["TEXT_MODEL_BASE_URL"] == "https://exported/v1"  # already exported: wins
    assert "AWS_SECRET_ACCESS_KEY" not in os.environ
    assert "OPENAI_API_KEY" not in os.environ


def test_walks_up_then_falls_back_to_extra_dirs(monkeypatch, tmp_path):
    (tmp_path / "skill").mkdir()
    (tmp_path / "skill" / ".env").write_text("TYPESAFE_MODEL=from-skill\n")
    work = tmp_path / "work" / "deep"
    work.mkdir(parents=True)
    monkeypatch.delenv("TYPESAFE_MODEL", raising=False)
    monkeypatch.chdir(work)
    assert load_env(tmp_path / "skill") == tmp_path / "skill" / ".env"
    assert os.environ["TYPESAFE_MODEL"] == "from-skill"


def test_no_file_is_fine(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    load_env(tmp_path / "nowhere")  # must not raise
