import subprocess
import sys

from bcagent import ui
from bcagent.session import Session


def big_file(n=3000):
    return "".join(f"def f{i}():\n    return {i}\n\n" for i in range(n))


def test_whole_rewrite_of_truncated_file_is_blocked(make_session):
    s = make_session("FILE: big.py\n```python\nprint('a brand new program')\nprint('that replaces it all')\n```\n")
    original = big_file()
    (s.project.root / "big.py").write_text(original)
    s.turn("clean up @big.py")
    assert "big.py" in s.project.truncated
    assert (s.project.root / "big.py").read_text(encoding="utf-8") == original, "nothing deleted"
    assert any(e["kind"] == "blocked" for e in s.events)
    assert len(s.client.prompts) == 1, "no repair loop for a safety block"


def test_truncated_file_hint_tells_model(make_session):
    s = make_session("Sure.")
    (s.project.root / "big.py").write_text(big_file())
    s.turn("clean up @big.py")
    assert "too long and was only partly shown" in s.client.prompts[0][-1]["content"]


def test_search_replace_on_truncated_file_still_works(make_session):
    s = make_session("FILE: big.py\n```python\n<<<<<<< SEARCH\ndef f0():\n    return 0\n=======\ndef f0():\n"
                     "    return 100\n>>>>>>> REPLACE\n```\n", agent="bulldog")
    (s.project.root / "big.py").write_text(big_file())
    s.turn("change f0 in @big.py")
    text = (s.project.root / "big.py").read_text(encoding="utf-8")
    assert "return 100" in text and "def f2999" in text


def test_unseen_file_shrinking_is_blocked(make_session):
    s = make_session("FILE: other.py\n```python\nprint('hi')\n```\n")
    original = "".join(f"x{i} = {i}\n" for i in range(50))
    (s.project.root / "other.py").write_text(original)
    s.project.relevant = lambda prompt: []   # the model is not shown other.py
    s.turn("do something")
    assert (s.project.root / "other.py").read_text(encoding="utf-8") == original


def test_normal_edit_applies(make_session):
    s = make_session("FILE: calc.py\n```python\ndef add(a, b):\n    return a + b\n```\n")
    (s.project.root / "calc.py").write_text("def add(a, b):\n    return a - b\n")
    s.turn("fix @calc.py")
    assert "a + b" in (s.project.root / "calc.py").read_text(encoding="utf-8")


def test_learn_mode_never_edits(make_session):
    s = make_session("FILE: calc.py\n```python\nprint('solution')\n```\n")
    s.learn = True
    (s.project.root / "calc.py").write_text("x = 1\n")
    s.turn("solve my homework in @calc.py")
    assert (s.project.root / "calc.py").read_text(encoding="utf-8") == "x = 1\n"
    assert "LEARNING MODE" in s.client.prompts[0][0]["content"]


def test_review_does_not_edit(make_session):
    s = make_session("1. What works well\nFILE: calc.py\n```python\nprint('rewritten')\n```\n")
    (s.project.root / "calc.py").write_text("x = 1\n")
    s.review_code("@calc.py")
    assert (s.project.root / "calc.py").read_text(encoding="utf-8") == "x = 1\n"
    assert "instructor" in s.client.prompts[0][-1]["content"]
    assert s.no_edits is False


def test_export_log(make_session):
    s = make_session("FILE: hello.py\n```python\nprint('hi')\n```\n")
    s.turn("create hello.py")
    path = s.export("")
    text = path.read_text(encoding="utf-8")
    assert "# bcagent session log" in text and "create hello.py" in text and "Edit applied" in text


def test_save_and_resume(make_session, tmp_path):
    s = make_session("Hello!")
    s.turn("first question")
    s2 = make_session()
    s2.session_id = "later"
    saved = s2.saved_sessions()
    assert saved and saved[0]["title"] == "first question"
    s2.restore(saved[0])
    assert s2.history[0]["content"] == "first question"


def test_run_tests_fix_loop(make_session, monkeypatch):
    s = make_session("FILE: calc.py\n```python\ndef add(a, b):\n    return a + b\n```\n")
    root = s.project.root
    (root / "calc.py").write_text("def add(a, b):\n    return a - b\n")
    (root / "test_calc.py").write_text("from calc import add\n\ndef test_add():\n    assert add(2, 3) == 5\n")
    monkeypatch.setattr("bcagent.session.python_for", lambda cwd: sys.executable)
    assert s.run_tests() is True
    assert "a + b" in (root / "calc.py").read_text(encoding="utf-8")


def test_guard_covers_code_saved_without_file_line(make_session):
    # the model forgets the FILE: line; bcagent's "save the code" path must still be guarded
    s = make_session("Here you go:\n```python\nprint('rewritten from scratch')\n" + "# filler\n" * 5 + "```\n")
    original = big_file()
    (s.project.root / "big.py").write_text(original)
    s.turn("make @big.py faster")
    assert (s.project.root / "big.py").read_text(encoding="utf-8") == original


def test_notebook_cell_removal_warns(make_session):
    import json
    s = make_session("FILE: lab.ipynb\n```python\n# %%\nprint('only')\n```\n")
    nb = {"nbformat": 4, "nbformat_minor": 5, "metadata": {}, "cells": [
        {"cell_type": "code", "id": "a", "metadata": {}, "source": ["x = 1"], "outputs": [], "execution_count": None},
        {"cell_type": "code", "id": "b", "metadata": {}, "source": ["y = 2"], "outputs": [], "execution_count": None}]}
    (s.project.root / "lab.ipynb").write_text(json.dumps(nb))
    from bcagent.edits import parse_response
    s.project.context("x @lab.ipynb", 10000)
    edits, _, _ = parse_response(s.client.answers[0], s.project)
    s.guard(edits)
    assert "removed 1 code cell" in edits[0].warning


def test_model_prefixing_project_folder_is_stripped(make_session):
    s = make_session("FILE: proj/calc.py\n```python\nx = 2\n```\n")
    (s.project.root / "calc.py").write_text("x = 1\n")
    s.turn("fix calc.py")
    assert (s.project.root / "calc.py").read_text(encoding="utf-8") == "x = 2\n"
    assert not (s.project.root / "proj").exists()


def test_pytest_startup_crash_is_not_sent_to_model(make_session, monkeypatch):
    s = make_session("FILE: calc.py\n```python\nx = 2\n```\n")
    calls = []

    def fake_run(cmd, cwd, extra=None):
        calls.append(extra)
        return 1, "Traceback (most recent call last):\nImportError: cannot import name 'X' from plugin"
    monkeypatch.setattr("bcagent.session.run_command", fake_run)
    assert s.run_tests() is False
    assert calls == [{}, {"PYTEST_DISABLE_PLUGIN_AUTOLOAD": "1"}] and not s.client.prompts


def test_big_file_function_edit_is_spliced(make_session):
    s = make_session("FILE: big.py\n```python\ndef f7():\n    return 700\n\n\ndef helper():\n    return 1\n```\n")
    (s.project.root / "big.py").write_text(big_file() + 'if __name__ == "__main__":\n    print(f7())\n')
    s.turn("make f7 in @big.py return 700 and add a helper")
    assert s.project.sections == {"big.py": ["f7"]}
    prompt = s.client.prompts[0][-1]["content"]
    assert "def f7" in prompt and "def f2999" not in prompt and "only f7 is shown" in prompt
    text = (s.project.root / "big.py").read_text(encoding="utf-8")
    assert "return 700" in text and "def f2999" in text and text.count("def f") == 3000
    assert text.index("def helper") < text.index("if __name__")


def test_lazy_partial_rewrite_is_spliced(make_session):
    s = make_session("FILE: tools.py\n```python\ndef b():\n    return 20\n```\n")
    original = "".join(f"def {n}():\n    return {i}\n\n\n" for i, n in enumerate("abcdef"))
    (s.project.root / "tools.py").write_text(original)
    s.turn("make b in @tools.py return 20")
    text = (s.project.root / "tools.py").read_text(encoding="utf-8")
    assert "return 20" in text and all(f"def {n}()" in text for n in "abcdef")


def test_asking_to_remove_functions_is_respected(make_session):
    s = make_session("FILE: tools.py\n```python\ndef a():\n    return 0\n```\n")
    (s.project.root / "tools.py").write_text("".join(f"def {n}():\n    return 1\n\n\n" for n in "abcdef"))
    s.turn("remove every function except a from @tools.py")
    text = (s.project.root / "tools.py").read_text(encoding="utf-8")
    assert "def b" not in text


def test_lazy_answer_on_truncated_file_keeps_everything(make_session):
    s = make_session("FILE: big.py\n```python\ndef f0():\n    return 100\n```\n")
    (s.project.root / "big.py").write_text(big_file())
    s.turn("clean up @big.py")
    text = (s.project.root / "big.py").read_text(encoding="utf-8")
    assert "return 100" in text and text.count("def f") == 3000
