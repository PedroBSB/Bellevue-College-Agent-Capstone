"""Course profiles: one file where an instructor sets how bcagent behaves for a class.

The profile is a Markdown file with settings at the top and notes for the agent below:

    ---
    course: CS 110 – Introduction to Python
    instructor: Prof. Rivera
    learn_mode: locked          # off | on | locked (students can't turn it off)
    allowed_agents: bulldog, explainer, tester
    default_agent: bulldog
    allow_mcp: false
    session_log: auto           # off | auto (log saved to bcagent-logs/ when bcagent exits)
    exam_start: 2026-12-10 09:00
    exam_end: 2026-12-10 11:00
    exam_mode: blocked          # blocked (no AI help) | hints (explain errors and concepts only)
    ---
    This week's lab is about loops. Students may not use list comprehensions yet.

bcagent reads `.bcagent/course.md` in the project folder (ship it in the assignment's starter
repository) or `~/.bcagent/course.md` (installed with `bcagent course install <file|url>`, for lab
computers). The project file wins.

This guides students; it is not proctoring software. Someone who deletes the file gets a normal
bcagent, but every exported session log records the course name and the profile's fingerprint, so
an instructor can see whether the official profile was used.
"""
from __future__ import annotations

import hashlib
import re
import urllib.request
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import List, Optional

from .config import HOME, ensure_home

PROJECT_FILE = Path(".bcagent") / "course.md"
USER_FILE = HOME / "course.md"

TEMPLATE = """---
course: CS 110 – Introduction to Python      # shown on the welcome screen and in session logs
instructor: Your Name
learn_mode: on            # off: normal bcagent · on: hints instead of solutions · locked: on, and students can't turn it off
allowed_agents: bulldog, explainer, tester    # leave empty to allow every agent
default_agent: bulldog
allow_mcp: false          # true lets students use MCP servers (web pages, other tools)
session_log: auto         # auto: save a log of every session in bcagent-logs/ (students hand it in) · off
exam_start:               # e.g. 2026-12-10 09:00 (local time) – leave empty when there is no exam
exam_end:                 # e.g. 2026-12-10 11:00
exam_mode: blocked        # during the exam: blocked (no AI help at all) · hints (explain errors and concepts only)
---
Notes for the agent about this course or assignment. They are sent with every request.
For example:
- This is an introductory course. Use only for-loops, while-loops, lists and functions.
- Students have not learned classes or list comprehensions yet; don't use them.
- Explain like you would to a first-year student.
"""

EXAM_HINT_RULES = """EXAM MODE: the student is taking an exam. You may ONLY:
- explain what an error message means in general terms,
- explain a Python concept or built-in function with a generic example unrelated to their task.
Never write, fix or complete code for their task, never look for bugs in their code, and never give the answer.
If they ask for more, say that this is not allowed during the exam."""


@dataclass
class Course:
    name: str = ""
    instructor: str = ""
    learn_mode: str = "off"            # off | on | locked
    allowed_agents: List[str] = field(default_factory=list)
    default_agent: str = ""
    allow_mcp: bool = True
    session_log: str = "off"           # off | auto
    exam_start: Optional[datetime] = None
    exam_end: Optional[datetime] = None
    exam_mode: str = "blocked"         # blocked | hints
    notes: str = ""
    source: str = ""
    fingerprint: str = ""

    # -- rules ------------------------------------------------------------------
    @property
    def learn_locked(self) -> bool:
        return self.learn_mode == "locked" or self.exam_active()

    def exam_active(self, now: Optional[datetime] = None) -> bool:
        if not self.exam_start or not self.exam_end:
            return False
        now = now or datetime.now()
        return self.exam_start <= now <= self.exam_end

    def exam_blocked(self, now: Optional[datetime] = None) -> bool:
        return self.exam_active(now) and self.exam_mode != "hints"

    def agent_allowed(self, name: str) -> bool:
        return not self.allowed_agents or name in self.allowed_agents

    def summary(self) -> str:
        bits = [self.name or "course profile"]
        if self.exam_active():
            bits.append(f"exam until {self.exam_end:%H:%M} ({'no AI help' if self.exam_blocked() else 'hints only'})")
        elif self.learn_mode != "off":
            bits.append("learning mode" + (" (locked)" if self.learn_mode == "locked" else ""))
        return " · ".join(bits)


def _parse_time(text: str) -> Optional[datetime]:
    text = text.strip()
    if not text:
        return None
    for fmt in ("%Y-%m-%d %H:%M", "%Y-%m-%dT%H:%M", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d"):
        try:
            return datetime.strptime(text, fmt)
        except ValueError:
            continue
    raise ValueError(f"can't read the date '{text}' – use YYYY-MM-DD HH:MM, e.g. 2026-12-10 09:00")


def _bool(text: str, default: bool) -> bool:
    t = text.strip().lower()
    if t in ("true", "yes", "on", "1"):
        return True
    if t in ("false", "no", "off", "0"):
        return False
    return default


def parse(text: str, source: str = "") -> Course:
    meta: dict = {}
    body = text
    m = re.match(r"^﻿?---\s*\n(.*?)\n---\s*\n?(.*)$", text, re.S)
    if m:
        for line in m.group(1).splitlines():
            if ":" in line and not line.lstrip().startswith("#"):
                k, v = line.split(":", 1)
                v = re.sub(r"\s+#.*$", "", v) if not re.match(r"\s*#", v) else ""
                meta[k.strip().lower()] = v.strip().strip("\"'")
        body = m.group(2)
    learn = meta.get("learn_mode", "off").lower() or "off"
    if learn not in ("off", "on", "locked"):
        learn = "on" if _bool(learn, False) else "off"
    c = Course(
        name=meta.get("course", "") or meta.get("name", ""),
        instructor=meta.get("instructor", ""),
        learn_mode=learn,
        allowed_agents=[a.strip() for a in re.split(r"[,\s]+", meta.get("allowed_agents", "")) if a.strip()],
        default_agent=meta.get("default_agent", ""),
        allow_mcp=_bool(meta.get("allow_mcp", ""), True),
        session_log="auto" if meta.get("session_log", "off").lower() in ("auto", "on", "true", "required") else "off",
        exam_start=_parse_time(meta.get("exam_start", "")),
        exam_end=_parse_time(meta.get("exam_end", "")),
        exam_mode="hints" if meta.get("exam_mode", "").lower() == "hints" else "blocked",
        notes=body.strip(),
        source=source,
        fingerprint=hashlib.sha256(text.encode("utf-8")).hexdigest()[:12],
    )
    return c


def find(root: Path) -> Optional[Path]:
    for p in (Path(root) / PROJECT_FILE, USER_FILE):
        if p.is_file():
            return p
    return None


def load(root: Path) -> Optional[Course]:
    path = find(root)
    if not path:
        return None
    return parse(path.read_text(encoding="utf-8"), str(path))


def install(spec: str) -> Path:
    """Install a profile for every project on this computer (~/.bcagent/course.md)."""
    if spec.startswith(("http://", "https://")):
        with urllib.request.urlopen(spec, timeout=30) as r:
            text = r.read().decode("utf-8")
    else:
        text = Path(spec).expanduser().read_text(encoding="utf-8")
    parse(text)  # validate before saving
    ensure_home()
    USER_FILE.write_text(text, encoding="utf-8")
    return USER_FILE


def create(root: Path) -> Path:
    path = Path(root) / PROJECT_FILE
    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.exists():
        path.write_text(TEMPLATE, encoding="utf-8")
    return path
