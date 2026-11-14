"""`bcagent logs check <folder>`: read the session logs students hand in.

Point it at a folder of submissions (one subfolder per student, as most learning-management
systems and GitHub Classroom download them, or a flat folder of logs). For every student it shows:
sessions, time spent, prompts, edits applied, whether learning mode was on, whether the official
course profile was used (fingerprint), whether the log was edited by hand (checksum), and any
requests made during the exam window. The table is also saved as a CSV file for a spreadsheet.
"""
from __future__ import annotations

import csv
import hashlib
import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, List, Optional

from rich.markup import escape
from rich.table import Table

from . import course as course_mod, ui
from .ui import console

HEADER = "# bcagent session log"
CHECKSUM = re.compile(r"\n<!-- bcagent-log-sha256: ([0-9a-f]{64}) -->\s*$")


@dataclass
class SessionLog:
    path: Path
    student: str
    start: Optional[datetime] = None
    end: Optional[datetime] = None
    version: str = ""
    agent: str = ""
    model: str = ""
    learn: bool = False
    course: str = ""
    fingerprint: str = ""
    prompts: List[datetime] = field(default_factory=list)
    edits: int = 0
    commands: int = 0
    blocked: int = 0
    checksum: str = "missing"     # ok | edited | missing (older bcagent)

    @property
    def minutes(self) -> int:
        if self.start and self.end:
            return max(0, int((self.end - self.start).total_seconds() // 60))
        return 0


def parse(path: Path, student: str) -> Optional[SessionLog]:
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    if not text.startswith(HEADER):
        return None
    log = SessionLog(path, student)
    m = CHECKSUM.search(text)
    if m:
        body = text[:m.start()]
        log.checksum = "ok" if hashlib.sha256(body.encode("utf-8")).hexdigest() == m.group(1) else "edited"
    m = re.search(r"^- Date: (\d{4}-\d{2}-\d{2}) (\d{2}:\d{2}) – (\d{2}:\d{2})", text, re.M)
    day = None
    if m:
        day = m.group(1)
        log.start = datetime.strptime(f"{day} {m.group(2)}", "%Y-%m-%d %H:%M")
        log.end = datetime.strptime(f"{day} {m.group(3)}", "%Y-%m-%d %H:%M")
        if log.end < log.start:
            log.end += timedelta(days=1)
    m = re.search(r"^- bcagent v(\S+) · agent (\S+) · model (\S+)", text, re.M)
    if m:
        log.version, log.agent, log.model = m.groups()
    log.learn = bool(re.search(r"^- Learning mode: on", text, re.M))
    m = re.search(r"^- Course profile: (.*?) · instructor .*? · fingerprint `([0-9a-f]+)`", text, re.M)
    if m:
        log.course, log.fingerprint = m.groups()
    prev = log.start
    for m in re.finditer(r"^## (?:(\d{4}-\d{2}-\d{2}) )?(\d{2}:\d{2}:\d{2}) · You$", text, re.M):
        d = m.group(1) or day
        if not d:
            continue
        t = datetime.strptime(f"{d} {m.group(2)}", "%Y-%m-%d %H:%M:%S")
        while prev and not m.group(1) and t < prev - timedelta(minutes=1):
            t += timedelta(days=1)   # the session went past midnight
        log.prompts.append(t)
        prev = t
    if log.prompts and log.end and log.prompts[-1] > log.end:
        log.end = log.prompts[-1]
    log.edits = len(re.findall(r"^\*\*Edit applied:\*\*", text, re.M))
    log.commands = len(re.findall(r"^\*\*Ran\*\* `", text, re.M))
    log.blocked = len(re.findall(r"^\*\*blocked:\*\* exam mode", text, re.M))
    return log


def collect(folder: Path) -> List[SessionLog]:
    """All session logs under `folder`. The student is the first subfolder (submissions/<student>/…);
    logs directly in `folder` are grouped by their file name's folder."""
    folder = Path(folder)
    out = []
    for path in sorted(folder.rglob("*.md")):
        rel = path.relative_to(folder)
        student = rel.parts[0] if len(rel.parts) > 1 else path.stem
        log = parse(path, student)
        if log:
            out.append(log)
    return out


@dataclass
class Student:
    name: str
    sessions: List[SessionLog] = field(default_factory=list)

    def row(self, expected: Optional[str], exam: Optional[course_mod.Course]) -> Dict[str, str]:
        s = self.sessions
        prompts = [p for x in s for p in x.prompts]
        fps = {x.fingerprint for x in s}
        if not expected:
            profile = ", ".join(sorted(f or "none" for f in fps))
        elif fps == {expected}:
            profile = "ok"
        elif "" in fps and len(fps) == 1:
            profile = "MISSING"
        else:
            profile = "DIFFERENT"
        during = [p for p in prompts if exam and exam.exam_start and exam.exam_end and
                  exam.exam_start <= p <= exam.exam_end]
        checks = {x.checksum for x in s}
        flags = []
        if profile in ("MISSING", "DIFFERENT"):
            flags.append(f"profile {profile.lower()}")
        if "edited" in checks:
            flags.append("log edited")
        if during:
            flags.append(f"{len(during)} request(s) during the exam")
        learn = {x.learn for x in s}
        starts = [x.start for x in s if x.start]
        return {
            "student": self.name,
            "sessions": str(len(s)),
            "first": min(starts).strftime("%Y-%m-%d %H:%M") if starts else "",
            "last": max(starts).strftime("%Y-%m-%d %H:%M") if starts else "",
            "minutes": str(sum(x.minutes for x in s)),
            "prompts": str(len(prompts)),
            "edits": str(sum(x.edits for x in s)),
            "commands": str(sum(x.commands for x in s)),
            "learning_mode": "on" if learn == {True} else "off" if learn == {False} else "mixed",
            "profile": profile,
            "log_checksum": "ok" if checks == {"ok"} else "EDITED" if "edited" in checks else "not available",
            "exam_requests": str(len(during)),
            "exam_blocked": str(sum(x.blocked for x in s)),
            "agents": ", ".join(sorted({x.agent for x in s if x.agent})),
            "flags": "; ".join(flags),
        }


def check(folder: Path, profile: Optional[Path] = None, csv_path: Optional[Path] = None) -> int:
    folder = Path(folder)
    if not folder.is_dir():
        console.print(f"[err]✗ not a folder: {folder}[/]")
        return 1
    if profile is None and (Path.cwd() / course_mod.PROJECT_FILE).is_file():
        profile = Path.cwd() / course_mod.PROJECT_FILE
    course = None
    if profile:
        text = Path(profile).read_text(encoding="utf-8")
        course = course_mod.parse(text, str(profile))
    logs = collect(folder)
    if not logs:
        console.print(f"[warn]No bcagent session logs found in {escape(str(folder))}[/] [muted](they are files "
                      "starting with '# bcagent session log', usually in bcagent-logs/)[/]")
        return 1
    students: Dict[str, Student] = {}
    for log in logs:
        students.setdefault(log.student, Student(log.student)).sessions.append(log)
    rows = [st.row(course.fingerprint if course else None, course) for st in
            sorted(students.values(), key=lambda x: x.name.lower())]
    if course:
        console.print(f"Course profile [bc]{escape(course.name or '(unnamed)')}[/] · fingerprint "
                      f"[bc]{course.fingerprint}[/]" + (f" · exam {course.exam_start:%b %d %H:%M}–"
                                                        f"{course.exam_end:%H:%M}" if course.exam_start and
                                                        course.exam_end else ""))
    else:
        console.print("[muted]No course profile given (--profile course.md) – fingerprints are listed but "
                      "not compared.[/]")
    t = Table(border_style=ui.BC_BLUE_LIGHT, header_style="bc")
    for col in ("student", "sess.", "min", "prompts", "edits", "learn", "profile", "log", "flags"):
        t.add_column(col, no_wrap=col not in ("flags", "student"), overflow="fold")
    for r in rows:
        style = "err" if r["flags"] else ""
        t.add_row(escape(r["student"]), r["sessions"], r["minutes"], r["prompts"], r["edits"], r["learning_mode"],
                  r["profile"], r["log_checksum"], f"[{style}]{escape(r['flags'])}[/]" if style else "")
    console.print(t)
    flagged = sum(1 for r in rows if r["flags"])
    console.print(f"{len(rows)} student(s), {len(logs)} session log(s)" +
                  (f" · [err]{flagged} flagged[/]" if flagged else " · [ok]nothing flagged[/]"))
    csv_path = Path(csv_path or "bcagent-logs-report.csv")
    with open(csv_path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        w.writeheader()
        w.writerows(rows)
    console.print(f"[muted]Saved the table to[/] [bc]{csv_path}[/] [muted](opens in Excel / Google Sheets)[/]")
    console.print("[muted]Logs are plain text: the checksum catches edits by hand, but a determined student "
                  "could rewrite a log. Treat flags as a reason to ask, not as proof.[/]")
    return 0


def command(argv: List[str]) -> int:
    import argparse
    p = argparse.ArgumentParser(prog="bcagent logs", description="Read the session logs students hand in")
    sub = p.add_subparsers(dest="cmd")
    pc = sub.add_parser("check", help="summarize a folder of submissions")
    pc.add_argument("folder")
    pc.add_argument("--profile", help="the course profile you gave students (default: ./.bcagent/course.md)")
    pc.add_argument("--csv", help="where to save the table (default: bcagent-logs-report.csv)")
    a = p.parse_args(argv)
    if a.cmd != "check":
        p.print_help()
        return 2
    return check(Path(a.folder), Path(a.profile) if a.profile else None, Path(a.csv) if a.csv else None)
