"""Every test runs with a private BCAGENT_HOME, no real GitHub/Google credentials and no real ~/.ssh."""
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

_HOME = tempfile.mkdtemp(prefix="bcagent-test-home-")
os.environ["BCAGENT_HOME"] = _HOME
os.environ["BCAGENT_NO_UPDATE"] = "1"
os.environ["BCAGENT_NO_PACKAGES"] = "1"
for var in ("GH_TOKEN", "GITHUB_TOKEN", "BCAGENT_GOOGLE_TOKEN", "BCAGENT_LEARN"):
    os.environ.pop(var, None)
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest  # noqa: E402

from bcagent import git as git_mod, ui  # noqa: E402


@pytest.fixture(autouse=True)
def isolate(monkeypatch, tmp_path):
    monkeypatch.setattr(git_mod, "_gh_cli_token", lambda: None)   # never use the developer's gh login
    monkeypatch.setattr(git_mod, "TOKEN_FILE", tmp_path / "gh-token.json")
    monkeypatch.setattr(git_mod, "SSH_DIR", tmp_path / ".ssh")
    monkeypatch.setattr(git_mod.webbrowser, "open", lambda *a, **k: True)
    monkeypatch.setattr(ui, "is_interactive", lambda: False)
    yield


class FakeClient:
    """Stands in for Ollama: returns the queued answers in order."""

    def __init__(self, *answers):
        self.answers = list(answers)
        self.prompts = []
        self.last_stats = {}

    def chat_stream(self, model, messages, options=None):
        self.prompts.append(messages)
        yield self.answers.pop(0) if self.answers else "OK."


@pytest.fixture
def make_session(tmp_path):
    from bcagent.agents import all_agents
    from bcagent.project import Project
    from bcagent.session import Session

    def make(*answers, agent="bulldog", auto=True, root=None, cfg=None):
        root = Path(root or tmp_path / "proj")
        root.mkdir(parents=True, exist_ok=True)
        s = Session(Project(root), all_agents(root)[agent],
                    dict({"num_ctx": 8192, "max_tokens": 4096, "max_repairs": 1}, **(cfg or {})),
                    FakeClient(*answers), auto=auto, print_mode=True)
        return s
    return make


def git(*args, cwd):
    return subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, check=True).stdout.strip()


@pytest.fixture
def repo(tmp_path):
    """A git repo with a local bare 'remote'."""
    remote = tmp_path / "remote.git"
    subprocess.run(["git", "init", "-q", "--bare", str(remote)], check=True)
    root = tmp_path / "work"
    root.mkdir()
    subprocess.run(["git", "init", "-q", str(root)], check=True)
    git("checkout", "-q", "-b", "main", cwd=root)
    git("config", "user.name", "Test", cwd=root)
    git("config", "user.email", "test@example.com", cwd=root)
    git("config", "commit.gpgsign", "false", cwd=root)
    return root, remote


SAMPLE_NB = {
    "nbformat": 4, "nbformat_minor": 0, "metadata": {"colab": {"provenance": []}},
    "cells": [
        {"cell_type": "markdown", "metadata": {"id": "m1"}, "source": ["# Title\n", "\n", "Some *text*"]},
        {"cell_type": "code", "metadata": {"id": "c1"}, "execution_count": 1,
         "source": ["!pip install pandas\n", "import pandas as pd"],
         "outputs": [{"output_type": "stream", "name": "stdout", "text": ["ok"]}]},
        {"cell_type": "code", "metadata": {"id": "c2"}, "execution_count": 2, "source": ["x = 1\n", "print(x)"],
         "outputs": [{"output_type": "stream", "name": "stdout", "text": ["1"]}]},
    ]}


def sample_nb():
    return json.loads(json.dumps(SAMPLE_NB))
