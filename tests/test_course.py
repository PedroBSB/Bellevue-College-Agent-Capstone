from datetime import datetime, timedelta

import pytest

from bcagent import cli, course as C, hardware

PROFILE = """---
course: CS 110 – Intro to Python   # comment
instructor: Prof. Rivera
learn_mode: locked
allowed_agents: bulldog, explainer
default_agent: bulldog
allow_mcp: false
session_log: auto
exam_start: {start}
exam_end: {end}
exam_mode: {mode}
---
Students haven't learned list comprehensions yet.
"""


def write(root, start="", end="", mode="blocked", **over):
    text = PROFILE.format(start=start, end=end, mode=mode)
    for k, v in over.items():
        text = text.replace(f"{k}: ", f"{k}: {v}  # ", 1)
    (root / ".bcagent").mkdir(parents=True, exist_ok=True)
    (root / ".bcagent" / "course.md").write_text(text, encoding="utf-8")


def stamp(delta_min):
    return (datetime.now() + timedelta(minutes=delta_min)).strftime("%Y-%m-%d %H:%M")


def test_parse_template_and_profile(tmp_path):
    t = C.parse(C.TEMPLATE)
    assert t.name.startswith("CS 110") and t.learn_mode == "on" and t.allowed_agents == ["bulldog", "explainer", "tester"]
    assert t.exam_start is None and not t.allow_mcp and t.session_log == "auto"
    write(tmp_path)
    c = C.load(tmp_path)
    assert c.name == "CS 110 – Intro to Python" and c.instructor == "Prof. Rivera" and c.learn_locked
    assert c.allowed_agents == ["bulldog", "explainer"] and c.notes.startswith("Students") and len(c.fingerprint) == 12
    with pytest.raises(ValueError):
        C.parse("---\nexam_start: tomorrow\n---\n")


def test_locked_learning_mode_and_notes(make_session, tmp_path):
    root = tmp_path / "proj"
    write(root)
    s = make_session("FILE: a.py\n```python\nprint('answer')\n```\n", root=root)
    assert s.learn and s.edit_format == "none"
    s.turn("solve the homework in a.py")
    assert not (root / "a.py").exists()
    system = s.client.prompts[0][0]["content"]
    assert "LEARNING MODE" in system and "list comprehensions" in system
    cli.handle_slash("/learn off", s, None, {})
    assert s.learn, "locked"


def test_exam_blocked_and_hints(make_session, tmp_path):
    root = tmp_path / "proj"
    write(root, stamp(-10), stamp(50), "blocked")
    s = make_session("Should not be asked", root=root)
    s.turn("fix my code")
    assert not s.client.prompts and any(e["kind"] == "blocked" for e in s.events)
    write(root, stamp(-10), stamp(50), "hints")
    s = make_session("A concept.", root=root)
    s.turn("what does IndexError mean")
    assert "EXAM MODE" in s.client.prompts[0][0]["content"] and s.edit_format == "none"
    write(root, stamp(-120), stamp(-60), "blocked")   # exam is over
    s = make_session("ok", root=root)
    s.turn("hi")
    assert s.client.prompts and "EXAM MODE" not in s.client.prompts[0][0]["content"]


def test_main_refuses_disallowed_agent(tmp_path, monkeypatch, capsys):
    write(tmp_path)
    monkeypatch.chdir(tmp_path)
    seen = {}

    class FakeSession:
        def __init__(self, project, agent, cfg, client, auto=False, print_mode=False):
            seen["agent"] = agent.name
            raise SystemExit(0)
    monkeypatch.setattr(cli, "Session", FakeSession)
    monkeypatch.setattr(cli, "ensure_model", lambda c, m: True)
    with pytest.raises(SystemExit):
        cli.main(["-p", "-a", "bulldog-7b", "hi"])
    assert seen["agent"] == "bulldog" and "doesn't allow the agent bulldog-7b" in capsys.readouterr().out


def test_agents_models_mcp_restricted(make_session, tmp_path):
    root = tmp_path / "proj"
    write(root)
    s = make_session(root=root)
    before = s.agent.name
    cli.handle_slash("/agent bulldog-7b", s, None, {})
    cli.handle_slash("/model llama3:70b", s, None, {})
    cli.handle_slash("/mcp add fetch", s, None, {})
    assert s.agent.name == before and s.agent.model == "qwen2.5-coder:0.5b"
    from bcagent import mcp
    assert "fetch" not in mcp.configured(root)


def test_autosaved_log_records_course(make_session, tmp_path):
    root = tmp_path / "proj"
    write(root)
    s = make_session("Hint: use a loop.", root=root)
    s.turn("how do I sum a list?")
    path = s.autosave_log()
    text = path.read_text(encoding="utf-8")
    assert path.parent.name == "bcagent-logs" and "CS 110" in text and s.course.fingerprint in text


def test_install_and_command(tmp_path, monkeypatch):
    src = tmp_path / "c.md"
    src.write_text(C.TEMPLATE, encoding="utf-8")
    monkeypatch.setattr(C, "USER_FILE", tmp_path / "home-course.md")
    assert C.install(str(src)).exists()
    monkeypatch.chdir(tmp_path)
    assert C.load(tmp_path).name.startswith("CS 110")
    assert cli.course_command(["create"]) == 0 and (tmp_path / ".bcagent" / "course.md").exists()
    assert cli.course_command(["show"]) == 0 and cli.course_command(["remove"]) == 0


def test_recommend_tiers():
    assert hardware.recommend(4)[0] == "bulldog"
    assert hardware.recommend(8)[0] == "bulldog-1.5b"
    assert hardware.recommend(16)[0] == "bulldog-3b"
    assert hardware.recommend(64)[0] == "bulldog-7b"
    assert hardware.recommend(64, allowed=["bulldog", "bulldog-1.5b"])[0] == "bulldog-1.5b"
    assert hardware.total_ram_gb() is None or hardware.total_ram_gb() > 0.5


def test_suggest_once(make_session, monkeypatch):
    from bcagent import ui
    s = make_session()
    s.print_mode = False
    monkeypatch.setattr(ui, "is_interactive", lambda: True)
    monkeypatch.setattr(hardware, "total_ram_gb", lambda: 16.0)
    asked = []
    monkeypatch.setattr(ui, "choose", lambda q, o, cancel=None: asked.append(q) or 2)   # don't ask again
    cfg = {}
    hardware.maybe_suggest(s, cfg, None)
    hardware.maybe_suggest(s, cfg, None)
    assert len(asked) == 1 and cfg["recommend_asked"] is True and "bulldog-3b" in asked[0]


def test_learning_mode_hides_long_solutions(make_session, tmp_path):
    from bcagent.session import hide_solutions
    long = "Here:\n```python\n" + "\n".join(f"x{i} = {i}" for i in range(8)) + "\n```\nshort:\n```python\nlen(x)\n```"
    out = hide_solutions(long)
    assert "x7 = 7" not in out and "hid 8 lines" in out and "len(x)" in out
    short_solution = "```python\ndef average(nums):\n    if not nums:\n        return 0\n```"
    assert "def average" not in hide_solutions(short_solution)
    s = make_session(long)
    s.learn = True
    s.turn("solve it")
    assert "x7 = 7" not in s.history[-1]["content"], "the hidden solution isn't kept in the conversation either"
