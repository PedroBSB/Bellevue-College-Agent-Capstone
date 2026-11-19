import os

import pytest

from bcagent.project import Project


def _symlink(target, link):
    try:
        os.symlink(target, link)
    except (OSError, NotImplementedError):
        pytest.skip("this system does not allow creating symlinks")


def test_symlink_outside_project_is_skipped(tmp_path):
    # issues #2/#3: bcagent started in the home folder, which holds a symlink into Homebrew
    proj, outside = tmp_path / "proj", tmp_path / "outside"
    (proj / "bin").mkdir(parents=True)
    outside.mkdir()
    (outside / "claude").write_text("x")
    (proj / "a.py").write_text("print(1)\n")
    _symlink(outside / "claude", proj / "bin" / "claude")
    p = Project(proj)
    assert p.files() == ["a.py"]
    ctx, included = p.context("give me the forecast for tomorrow", 10000)
    assert "a.py" in ctx and "claude" not in ctx


def test_symlink_loop_is_skipped(tmp_path):
    (tmp_path / "a.py").write_text("print(1)\n")
    _symlink(tmp_path / "loop", tmp_path / "loop")
    assert Project(tmp_path).files() == ["a.py"]
