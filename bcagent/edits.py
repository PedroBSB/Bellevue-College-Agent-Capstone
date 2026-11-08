"""Turn a model answer into file edits and shell commands.

Tiny models are not reliable at JSON tool calling, so bcagent uses a forgiving text protocol:

    FILE: path/to/file.py
    ```python
    <complete file>
    ```

It also accepts paths in the fence info string (```python:app.py), markdown headings
(### app.py), a first-line comment (# app.py) and aider-style SEARCH/REPLACE blocks.
"""
from __future__ import annotations

import difflib
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional, Tuple

from .project import SPECIAL_NAMES, TEXT_EXT, Project

SHELL_LANGS = {"bash", "sh", "shell", "zsh", "console", "terminal", "powershell", "ps1", "ps",
               "cmd", "bat", "batch"}
KNOWN_EXT = TEXT_EXT | {".cfg", ".lock", ".svg"}
SR_RE = re.compile(r"<{5,9} ?SEARCH[^\n]*\n(.*?)\n?={5,9}[ \t]*\n(.*?)\n?>{5,9} ?REPLACE", re.S)
LAZY_RE = re.compile(r"(?i)(rest of (the )?(code|file|class|function)|existing code (here|remains|unchanged)"
                     r"|\.\.\.\s*\(?(unchanged|remaining|same as before))")


@dataclass
class Block:
    lang: str
    info: str
    body: str
    path: Optional[str]
    start: int
    end: int


@dataclass
class Edit:
    path: str
    new: str = ""
    old: Optional[str] = None      # None -> new file
    error: Optional[str] = None
    warning: Optional[str] = None

    @property
    def is_new(self) -> bool:
        return self.old is None

    def stats(self) -> Tuple[int, int]:
        added = removed = 0
        for line in difflib.ndiff((self.old or "").splitlines(), self.new.splitlines()):
            if line.startswith("+ "):
                added += 1
            elif line.startswith("- "):
                removed += 1
        return added, removed

    def diff_lines(self) -> List[str]:
        return list(difflib.unified_diff(
            (self.old or "").splitlines(), self.new.splitlines(),
            fromfile=f"a/{self.path}", tofile=f"b/{self.path}", lineterm="", n=3))


# -- path detection -----------------------------------------------------------

def valid_path(cand: Optional[str]) -> Optional[str]:
    if not cand:
        return None
    cand = cand.strip().strip("`*'\"()[]<>,;").rstrip(":").replace("\\", "/")
    if cand.startswith("./"):
        cand = cand[2:]
    if not cand or "://" in cand or ".." in cand.split("/") or cand.startswith("/"):
        return None
    if not re.fullmatch(r"[\w./-]+", cand):
        return None
    p = Path(cand)
    if p.suffix.lower() in KNOWN_EXT or p.name in SPECIAL_NAMES:
        return cand
    return None


def extract_path(line: str) -> Optional[str]:
    s = line.strip()
    if not s or len(s) > 160:
        return None
    m = re.search(r"(?i)\b(?:file(?:name)?|path)\s*[:=]\s*[`*\"']*([^\s`*\"']+)", s)
    if m and valid_path(m.group(1)):
        return valid_path(m.group(1))
    bare = re.sub(r"^[#>\-*+\d.\s]+", "", s).strip("*`_ :\"'")
    if bare and " " not in bare and valid_path(bare):
        return valid_path(bare)
    ticked = [valid_path(t) for t in re.findall(r"`([^`\s]+)`", s)]
    ticked = [t for t in ticked if t]
    if len(ticked) == 1:
        return ticked[0]
    words = {valid_path(w) for w in s.split()} - {None}
    if len(words) == 1 and s.endswith(":"):
        return words.pop()
    return None


def path_from_info(info: str) -> Optional[str]:
    for tok in re.split(r"[\s:=]+", info):
        p = valid_path(tok)
        if p:
            return p
    return None


def path_from_first_line(body: List[str]) -> Optional[str]:
    if not body:
        return None
    m = re.match(r"^\s*(?:#|//|--|<!--)\s*(?:file(?:name)?\s*:\s*)?([\w./\\-]+)\s*(?:-->)?\s*$", body[0])
    return valid_path(m.group(1)) if m else None


def split_blocks(text: str) -> List[Block]:
    lines = text.split("\n")
    blocks: List[Block] = []
    i, prev_end = 0, -1
    while i < len(lines):
        m = re.match(r"^\s*(`{3,}|~{3,})\s*(.*)$", lines[i])
        if not m:
            i += 1
            continue
        fence, info = m.group(1), m.group(2).strip()
        close = re.compile(r"^\s*" + re.escape(fence[0]) + "{" + str(len(fence)) + r",}\s*$")
        j, body = i + 1, []
        while j < len(lines) and not close.match(lines[j]):
            body.append(lines[j])
            j += 1
        path = path_from_info(info)
        if not path:  # look at up to 2 non-empty lines above the fence
            k, seen = i - 1, 0
            while k > prev_end and seen < 2:
                if lines[k].strip():
                    path = extract_path(lines[k])
                    seen += 1
                    if path:
                        break
                k -= 1
        if not path:
            path = path_from_first_line(body)
        first = info.split()[0].lower() if info else ""
        lang = "" if valid_path(first) else re.split(r"[:=]", first)[0]
        blocks.append(Block(lang, info, "\n".join(body), path, i, j))
        prev_end = j
        i = j + 1
    return blocks


# -- applying -----------------------------------------------------------------

def _replace(content: str, search: str, replace: str) -> Optional[str]:
    if search in content:
        return content.replace(search, replace, 1)
    if not search.strip():
        return content.rstrip("\n") + "\n" + replace + "\n"
    c_lines = content.split("\n")
    s_lines = [l.strip() for l in search.strip("\n").split("\n")]
    n = len(s_lines)
    for i in range(len(c_lines) - n + 1):
        if [l.strip() for l in c_lines[i:i + n]] == s_lines:
            return "\n".join(c_lines[:i] + replace.strip("\n").split("\n") + c_lines[i + n:])
    return None


def _current(project: Project, path: str) -> Optional[str]:
    try:
        p = project.resolve(path)
    except ValueError:
        return None
    return p.read_text(encoding="utf-8", errors="replace") if p.is_file() else None


def parse_response(text: str, project: Project):
    """Return (edits, commands, orphan_blocks)."""
    edits: dict = {}
    commands: List[str] = []
    orphans: List[Block] = []
    for b in split_blocks(text):
        has_sr = bool(SR_RE.search(b.body))
        if b.lang in SHELL_LANGS and not b.path and not has_sr:
            commands.extend(_commands_from(b))
            continue
        if not b.path:
            if b.body.strip():
                orphans.append(b)
            continue
        try:
            project.resolve(b.path)
        except ValueError as e:
            edits[b.path] = Edit(b.path, error=str(e))
            continue
        e = edits.get(b.path)
        if e is None:
            old = _current(project, b.path)
            e = edits[b.path] = Edit(b.path, new=old or "", old=old)
        if has_sr:
            for search, repl in SR_RE.findall(b.body):
                out = _replace(e.new, search, repl)
                if out is None:
                    e.error = "SEARCH block did not match the current file"
                    break
                e.new = out
        else:
            e.new = b.body.rstrip("\n") + "\n"
            if not e.is_new and LAZY_RE.search(e.new):
                e.warning = "the model may have left out parts of the file"
    result = [e for e in edits.values() if e.error or e.new != (e.old or "") or e.is_new]
    return result, commands, orphans


def _commands_from(b: Block) -> List[str]:
    lines = b.body.split("\n")
    prompt_style = any(l.lstrip().startswith(("$ ", "> ", "PS> ")) for l in lines)
    cmds, cur = [], ""
    for line in lines:
        s = line.strip()
        if prompt_style:
            if not s.startswith(("$ ", "> ", "PS> ")):
                continue
            s = s.split(" ", 1)[1]
        if not s or s.startswith("#") or s.startswith("REM "):
            continue
        if s.endswith("\\") or s.endswith("`"):
            cur += s[:-1] + " "
            continue
        cmds.append((cur + s).strip())
        cur = ""
    if cur.strip():
        cmds.append(cur.strip())
    return cmds


def write_edit(project: Project, edit: Edit) -> None:
    p = project.resolve(edit.path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(edit.new, encoding="utf-8")


def check_python(project: Project, path: str) -> Optional[str]:
    if not path.endswith(".py"):
        return None
    try:
        src = project.resolve(path).read_text(encoding="utf-8")
        compile(src, path, "exec")
    except SyntaxError as e:
        return f"SyntaxError on line {e.lineno}: {e.msg}"
    except (OSError, ValueError) as e:
        return str(e)
    return None
