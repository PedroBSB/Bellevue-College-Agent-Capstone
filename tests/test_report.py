from pathlib import Path

from bcagent import git as G, report, ui


def boom():
    raise ValueError(f"cannot open {Path.home()}/secret/project.py with token ghp_{'a' * 36}")


def get_error():
    try:
        boom()
    except ValueError as e:
        return e


def test_scrub_removes_home_tokens_emails():
    text = report.scrub(f"{Path.home()}/x token=abc123 ghp_{'b' * 36} ya29.{'c' * 30} me@example.com")
    assert str(Path.home()) not in text and "abc123" not in text and "ghp_" not in text
    assert "ya29." not in text and "me@example.com" not in text and "~/x" in text


def test_build_report_contents():
    title, body = report.build(get_error(), context="handling `x`", description="it broke", prompt="")
    assert title.startswith("Bug: ValueError")
    assert "Traceback" in body and "bcagent: v" in body and "it broke" in body
    assert str(Path.home()) not in body and "ghp_" + "a" * 36 not in body and "[REDACTED]" in body


def test_offer_sends_issue_from_user_account(monkeypatch):
    monkeypatch.setenv("GH_TOKEN", "ghp_" + "x" * 36)
    monkeypatch.setattr(ui, "is_interactive", lambda: True)
    answers = iter([0, 0, 0])   # yes report, don't include prompt (index 0 = No), send it
    monkeypatch.setattr(ui, "choose", lambda q, o, cancel=None: next(answers))
    monkeypatch.setattr(report.console, "input", lambda *a, **k: "I asked for a plot")
    calls = []

    def fake_api(method, path, body=None):
        calls.append((method, path, body))
        if path.startswith("/search/"):
            return {"items": []}, {}
        return {"html_url": "https://github.com/x/y/issues/1"}, {}
    monkeypatch.setattr(G, "api", fake_api)
    report._asked.clear()
    report.offer(get_error(), context="handling `plot`", prompt="secret prompt text")
    post = [c for c in calls if c[0] == "POST"][0]
    assert post[1] == f"/repos/{report.REPO}/issues" and post[2]["labels"] == ["bug"]
    assert "I asked for a plot" in post[2]["body"] and "secret prompt text" not in post[2]["body"]


def test_offer_duplicate_adds_comment(monkeypatch):
    monkeypatch.setenv("GH_TOKEN", "ghp_" + "x" * 36)
    monkeypatch.setattr(ui, "is_interactive", lambda: True)
    answers = iter([0, 0, 0])   # yes report, send, comment on the existing issue
    monkeypatch.setattr(ui, "choose", lambda q, o, cancel=None: next(answers))
    monkeypatch.setattr(report.console, "input", lambda *a, **k: "")
    calls = []

    def fake_api(method, path, body=None):
        calls.append((method, path))
        if path.startswith("/search/"):
            return {"items": [{"number": 7, "title": "Bug: ValueError", "html_url": "u7"}]}, {}
        return {"html_url": "u7#c"}, {}
    monkeypatch.setattr(G, "api", fake_api)
    report._asked.clear()
    report.offer(get_error())
    assert ("POST", f"/repos/{report.REPO}/issues/7/comments") in calls


def test_no_token_opens_browser(monkeypatch):
    monkeypatch.setattr(ui, "is_interactive", lambda: True)
    answers = iter([0, 0])      # send it, open in browser
    monkeypatch.setattr(ui, "choose", lambda q, o, cancel=None: next(answers))
    opened = []
    monkeypatch.setattr(report.webbrowser, "open", lambda u: opened.append(u))
    report.offer(None, description="the diff colors are wrong", ask=False)
    assert opened and "issues/new?" in opened[0] and "labels=bug" in opened[0]


def test_declining_forever(monkeypatch):
    monkeypatch.setattr(ui, "is_interactive", lambda: True)
    monkeypatch.setattr(ui, "choose", lambda q, o, cancel=None: 2)
    report._asked.clear()
    report.offer(get_error())
    from bcagent.config import load_config, save_config
    cfg = load_config()
    assert cfg["bug_reports"] is False
    cfg.pop("bug_reports")
    save_config(cfg)
