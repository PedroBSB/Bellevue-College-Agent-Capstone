import subprocess

import pytest
from conftest import FakeClient, git

from bcagent import git as G


class Sess:
    auto = True
    agent = type("A", (), {"model": "x"})()
    cfg = {}

    def __init__(self, *answers):
        self.client = FakeClient(*answers)
        self.notes = []


def test_full_lifecycle(repo):
    root, remote = repo
    g = G.GitCommands(root, Sess("```\nAdd hello script.\n```"))
    (root / "hello.py").write_text("print('hi')\n")
    (root / ".env").write_text("SECRET=1\n")
    g.git("")                                    # status works before the first commit
    g.git("commit")                              # message written by the (fake) model
    assert git("log", "-1", "--format=%s", cwd=root) == "Add hello script"
    assert ".env" not in git("ls-files", cwd=root), "secret files are not committed"
    with pytest.raises(G.GitError):
        g.git("push")                            # no remote yet
    g.git(f"remote {remote}")
    assert git("remote", "get-url", "origin", cwd=root) == str(remote), "local paths aren't GitHub shorthand"
    g.git("push")
    assert git("rev-parse", "HEAD", cwd=root) == git("rev-parse", "main", cwd=remote)
    (root / "hello.py").write_text("print('hello')\n")
    g.git('commit -m "Say hello"')
    g.git("uncommit")
    assert "hello.py" in git("status", "--porcelain", cwd=root)
    g.git("commit Say hello")
    g.git("push")
    with pytest.raises(G.GitError):
        g.git("uncommit")                        # already pushed -> use revert
    g.git("revert")
    assert git("log", "-1", "--format=%s", cwd=root).startswith('Revert "Say hello"')
    g.git("branch feature")
    (root / "f.py").write_text("x = 1\n")
    g.git("commit Add f")
    g.git("switch main")
    g.git("merge feature")
    assert (root / "f.py").exists()
    (root / "junk.txt").write_text("junk")
    g.git("discard all")
    assert not (root / "junk.txt").exists()
    g.git("tag v1.0")
    assert git("tag", cwd=root) == "v1.0"


def test_pull_conflict_explained(repo, tmp_path):
    root, remote = repo
    g = G.GitCommands(root, Sess())
    (root / "a.py").write_text("x = 0\n")
    g.git("commit init")
    g.git(f"remote {remote}")
    g.git("push")
    other = tmp_path / "other"
    subprocess.run(["git", "clone", "-q", "-b", "main", str(remote), str(other)], check=True)
    (other / "a.py").write_text("x = 'remote'\n")
    subprocess.run(["git", "-C", str(other), "-c", "user.name=O", "-c", "user.email=o@x", "commit", "-qam", "r"],
                   check=True)
    subprocess.run(["git", "-C", str(other), "push", "-q"], check=True)
    (root / "a.py").write_text("x = 'local'\n")
    g.git("commit local")
    with pytest.raises(G.GitError, match="pull"):
        g.git("push")                            # behind -> tells you to pull
    with pytest.raises(G.GitError, match="merge conflict in a.py"):
        g.git("pull")
    g.git("merge --abort")


def test_init_creates_gitignore(tmp_path):
    g = G.GitCommands(tmp_path)
    g.git("init")
    assert (tmp_path / ".git").is_dir() and ".env" in (tmp_path / ".gitignore").read_text(encoding="utf-8")


def test_auth_env_credential_helper(monkeypatch, tmp_path):
    monkeypatch.setenv("GH_TOKEN", "ghp_" + "x" * 36)
    env = G.auth_env({})
    r = subprocess.run(["git", "credential", "fill"], input="protocol=https\nhost=github.com\n\n",
                       capture_output=True, text=True, env=dict(__import__("os").environ, **env), cwd=tmp_path)
    assert "username=x-access-token" in r.stdout and "password=ghp_" in r.stdout


def test_to_url_and_parse():
    assert G.to_url("octo/hello") == "https://github.com/octo/hello.git"
    assert G.parse_github("git@github.com:octo/hello.git") == ("octo", "hello")
    assert G.https_url("git@github.com:octo/hello.git") == "https://github.com/octo/hello.git"
    assert "ssh" in G.friendly("git@github.com: Permission denied (publickey).")
