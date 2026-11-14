import csv
from datetime import datetime, timedelta

from bcagent import logs
from test_course import write


def make_log(make_session, root, answers=("A hint.",), prompts=("help me",)):
    s = make_session(*answers, root=root)
    for p in prompts:
        s.turn(p)
    return s.export(f"bcagent-logs/{s.session_id}-{id(s)}.md")


def test_check_flags_each_problem(make_session, tmp_path, monkeypatch, capsys):
    course_dir = tmp_path / "instructor"
    write(course_dir)                       # the official profile
    sub = tmp_path / "submissions"
    # alice: honest
    write(sub / "alice")
    make_log(make_session, sub / "alice", prompts=("what is a loop?", "and a list?"))
    # bob: edited his log by hand
    write(sub / "bob")
    path = make_log(make_session, sub / "bob")
    path.write_text(path.read_text(encoding="utf-8").replace("help me", "nothing"), encoding="utf-8")
    # chen: a different profile (learning mode changed)
    write(sub / "chen", learn_mode="off")
    make_log(make_session, sub / "chen")
    # dana: no profile at all
    (sub / "dana").mkdir(parents=True)
    make_log(make_session, sub / "dana")
    # eve: asked during the exam (hints mode allows requests, the log records them)
    now = datetime.now()
    win = ((now - timedelta(minutes=5)).strftime("%Y-%m-%d %H:%M"), (now + timedelta(minutes=30)).strftime("%Y-%m-%d %H:%M"))
    write(course_dir, *win, "hints")
    write(sub / "eve", *win, "hints")
    make_log(make_session, sub / "eve", prompts=("what does IndexError mean?",))
    monkeypatch.chdir(tmp_path)
    assert logs.check(sub, course_dir / ".bcagent" / "course.md", tmp_path / "report.csv") == 0
    rows = {r["student"]: r for r in csv.DictReader(open(tmp_path / "report.csv", encoding="utf-8"))}
    assert set(rows) == {"alice", "bob", "chen", "dana", "eve"}
    # alice/bob/chen were made before the exam window was added to the official profile: compare to it
    assert rows["eve"]["profile"] == "ok" and rows["eve"]["exam_requests"] == "1"
    assert rows["alice"]["prompts"] == "2" and rows["alice"]["log_checksum"] == "ok"
    assert rows["bob"]["log_checksum"] == "EDITED" and "log edited" in rows["bob"]["flags"]
    assert rows["chen"]["profile"] == "DIFFERENT" and rows["dana"]["profile"] == "MISSING"
    assert rows["chen"]["learning_mode"] == "off" and rows["alice"]["learning_mode"] == "on"


def test_parse_handles_old_logs_and_midnight(tmp_path):
    text = ("# bcagent session log\n\n- Date: 2026-11-13 23:50 – 00:20\n- Project: x\n"
            "- bcagent v1.6.0 · agent bulldog · model m (local)\n- Learning mode: off\n- Course profile: none\n"
            "- 2 prompt(s), 0 file edit(s) applied\n\n## 23:55:00 · You\n\nhi\n\n## 00:05:00 · You\n\nagain\n")
    f = tmp_path / "s" / "a.md"
    f.parent.mkdir()
    f.write_text(text, encoding="utf-8")
    log = logs.parse(f, "s")
    assert log.checksum == "missing" and log.minutes == 30
    assert log.prompts[1] == datetime(2026, 11, 14, 0, 5)


def test_empty_folder(tmp_path):
    assert logs.check(tmp_path) == 1
