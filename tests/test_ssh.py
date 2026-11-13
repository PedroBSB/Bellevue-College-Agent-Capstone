import shutil
import subprocess

import pytest

from bcagent import git as G, ui

pytestmark = pytest.mark.skipif(not shutil.which("ssh-keygen"), reason="ssh-keygen not installed")


@pytest.fixture
def scripted(monkeypatch):
    monkeypatch.setattr(ui, "is_interactive", lambda: True)
    monkeypatch.setattr(ui, "choose", lambda q, o, cancel=None: 0)
    monkeypatch.setattr(G.console, "input", lambda *a, **k: "")
    monkeypatch.setattr(G, "ensure_known_hosts", lambda: None)
    real = subprocess.call

    def call(cmd, *a, **k):
        if cmd[0] == "ssh-keygen":
            cmd = cmd + ["-N", "", "-q"]
        return real(cmd, *a, **k)
    monkeypatch.setattr(G.subprocess, "call", call)


def test_creates_key_and_uploads_with_api(scripted, monkeypatch):
    monkeypatch.setenv("GH_TOKEN", "ghp_" + "x" * 36)
    calls = []
    monkeypatch.setattr(G, "api", lambda m, p, b=None: (calls.append((m, p)), ([], {}))[1])
    checks = iter([(False, "Permission denied"), (True, "student")])
    monkeypatch.setattr(G, "ssh_check", lambda: next(checks))
    assert G.ssh_setup() is True
    assert (G.SSH_DIR / "id_ed25519.pub").read_text(encoding="utf-8").startswith("ssh-ed25519")
    assert ("POST", "/user/keys") in calls


def test_manual_upload_when_no_token(scripted, monkeypatch):
    copied, opened = [], []
    monkeypatch.setattr(G, "_copy", lambda t: copied.append(t) or True)
    monkeypatch.setattr(G.webbrowser, "open", lambda u: opened.append(u))
    checks = iter([(False, "denied"), (True, "student")])
    monkeypatch.setattr(G, "ssh_check", lambda: next(checks))
    assert G.ssh_setup() is True
    assert copied[0].startswith("ssh-ed25519") and opened == [G.SSH_NEW_KEY_URL]


def test_failed_ssh_push_offers_https(repo, monkeypatch):
    root, _ = repo
    subprocess.run(["git", "remote", "add", "origin", "git@github.com:someone/x.git"], cwd=root, check=True)
    monkeypatch.setattr(ui, "is_interactive", lambda: True)
    monkeypatch.setattr(ui, "choose", lambda q, o, cancel=None: 1)          # "switch to HTTPS"
    monkeypatch.setattr(G, "ssh_check", lambda: (False, "Permission denied (publickey)."))
    monkeypatch.setattr(G, "ensure_known_hosts", lambda: None)
    monkeypatch.setattr(G, "ensure_login", lambda: True)
    g = G.GitCommands(root)
    monkeypatch.setattr(g.repo, "run", lambda *a, **k: (128, "Permission denied (publickey)")
                        if a[0] == "ls-remote" else G.Repo.run(g.repo, *a, **k))
    g.net("ls-remote", "origin", quiet=True)
    assert g.repo.remote_url() == "https://github.com/someone/x.git"
