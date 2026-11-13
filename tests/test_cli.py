from bcagent import cli, ui


def test_action_only_request_runs_slash_commands(make_session, monkeypatch):
    s = make_session()
    ran = []
    monkeypatch.setattr(cli, "handle_slash", lambda line, *a: ran.append(line) or True)
    cli.run_request("commit and push", s, None, {})
    assert ran == ["/git commit", "/git push"] and not s.client.prompts


def test_mixed_request_runs_agent_then_action(make_session, monkeypatch):
    s = make_session("FILE: calc.py\n```python\nx = 2\n```\n")
    ran = []
    monkeypatch.setattr(cli, "handle_slash", lambda line, *a: ran.append(line) or True)
    cli.run_request("fix the bug in calc.py and commit", s, None, {})
    assert s.client.prompts[0][-1]["content"].count("Task: fix the bug in calc.py") == 1
    assert ran == ["/git commit"]


def test_plain_request_goes_to_agent(make_session, monkeypatch):
    s = make_session("Hi")
    monkeypatch.setattr(cli, "handle_slash", lambda *a: (_ for _ in ()).throw(AssertionError()))
    cli.run_request("write fizzbuzz in fizz.py", s, None, {})
    assert s.client.prompts


def test_slash_learn_review_export_bug(make_session, monkeypatch):
    s = make_session("Feedback.")
    cli.handle_slash("/learn", s, None, {})
    assert s.learn
    cli.handle_slash("/learn off", s, None, {})
    assert not s.learn
    (s.project.root / "a.py").write_text("x = 1\n")
    cli.handle_slash("/review @a.py", s, None, {})
    cli.handle_slash("/export log.md", s, None, {})
    assert (s.project.root / "log.md").exists()
    offered = []
    monkeypatch.setattr(cli.report_mod, "offer", lambda *a, **k: offered.append(k))
    cli.handle_slash("/bug the menu is broken", s, None, {})
    assert offered[0]["description"] == "the menu is broken"


def test_internal_error_offers_report(make_session, monkeypatch):
    s = make_session()
    lines = iter(["do something"])

    def read():
        try:
            return next(lines)
        except StopIteration:
            raise EOFError from None   # ctrl+d ends the session
    monkeypatch.setattr(cli, "make_prompt", lambda session: read)
    monkeypatch.setattr(ui, "is_interactive", lambda: True)
    monkeypatch.setattr(ui, "welcome", lambda *a, **k: None)
    monkeypatch.setattr(cli, "run_request", lambda *a: 1 / 0)
    offered = []
    monkeypatch.setattr(cli.report_mod, "offer", lambda e, **k: offered.append(e))
    cli.repl(s, None, {}, None)                  # must not crash
    assert isinstance(offered[0], ZeroDivisionError) and s.last_error[1] == "do something"


def test_mcp_add_saves_full_script_path(tmp_path, monkeypatch):
    from bcagent import mcp
    monkeypatch.setattr(mcp, "USER_FILE", tmp_path / "mcp.json")
    (tmp_path / "server.py").write_text("print()")
    mcp.add("demo", {"command": "python", "args": ["server.py", "--flag"]}, root=tmp_path)
    saved = mcp._read(tmp_path / "mcp.json")["demo"]["args"]
    assert saved == [str((tmp_path / "server.py").resolve()), "--flag"]
