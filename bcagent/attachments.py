"""Drag and drop files and images into the prompt (like Claude Code).

Dropping a file on a terminal window types (pastes) its path. Every terminal quotes it differently:

    macOS Terminal / iTerm2   /Users/me/My\\ Pictures/plot.png
    GNOME / VS Code (Linux)   '/home/me/My Pictures/plot.png'
    Windows Terminal / cmd    "C:\\Users\\me\\My Pictures\\plot.png"   (or C:\\x\\plot.png without spaces)
    some file managers        file:///home/me/plot.png

bcagent turns those paths into short labels: images become ``[Image #1]`` (sent to vision models),
files inside the project become ``@path`` mentions, other text files become ``[File #2: notes.txt]``
(their content is sent with the prompt).
"""
from __future__ import annotations

import base64
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Tuple
from urllib.parse import unquote, urlparse

IMAGE_EXT = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp"}
MAX_IMAGE_BYTES = 20 * 1024 * 1024
MAX_FILE_CHARS = 20000
VISION_HINT = "qwen2.5vl:3b"   # a small local model that can read images

_TOKEN = re.compile(r'"[^"]+"|\'[^\']+\'|(?:\\.|[^\s"\'])+')
LABEL = re.compile(r"\[(Image|File) #(\d+)(?:: [^\]]*)?\]")


@dataclass
class Attachment:
    num: int
    path: Path
    image: bool

    @property
    def label(self) -> str:
        return f"[Image #{self.num}]" if self.image else f"[File #{self.num}: {self.path.name}]"


def _clean(token: str, windows: bool) -> str:
    """A pasted token as a plain path (quotes, backslash escapes and file:// removed)."""
    if len(token) >= 2 and token[0] == token[-1] and token[0] in "\"'":
        token = token[1:-1]
    elif not windows:
        token = re.sub(r"\\(.)", r"\1", token)   # My\ File.png -> My File.png
    if token.lower().startswith("file://"):
        u = urlparse(token)
        token = unquote(u.path)
        if windows and re.match(r"^/[A-Za-z]:", token):
            token = token[1:]
    if token.startswith("~"):
        token = os.path.expanduser(token)
    return token


def _looks_like_path(text: str) -> bool:
    return bool(re.match(r"^(/|~|[A-Za-z]:[\\/]|\\\\|file://)", text))


def dropped_paths(text: str, windows: Optional[bool] = None) -> List[Tuple[str, Path]]:
    """(token as typed, existing file) for every absolute path in `text`."""
    windows = os.name == "nt" if windows is None else windows
    out = []
    for m in _TOKEN.finditer(text):
        raw = m.group(0)
        path = _clean(raw, windows)
        if not _looks_like_path(path):
            continue
        try:
            p = Path(path)
            if p.is_file():
                out.append((raw, p.resolve()))
        except (OSError, ValueError):
            continue
    return out


def starts_with_path(line: str) -> bool:
    """True when a line starting with "/" is a dropped file (/Users/...), not a /command."""
    first = _TOKEN.match(line.strip())
    return bool(first and dropped_paths(first.group(0)))


@dataclass
class Attachments:
    """The files dropped into this session, numbered like Claude Code ([Image #1], [File #2: ...])."""
    root: Path
    items: Dict[int, Attachment] = field(default_factory=dict)

    def add(self, path: Path) -> str:
        """The text to show in the prompt for a dropped file."""
        path = Path(path).resolve()
        image = path.suffix.lower() in IMAGE_EXT
        if not image:
            try:
                return "@" + path.relative_to(Path(self.root).resolve()).as_posix()
            except ValueError:
                pass
        for a in self.items.values():
            if a.path == path:
                return a.label
        a = Attachment(len(self.items) + 1, path, image)
        self.items[a.num] = a
        return a.label

    def convert(self, text: str) -> str:
        """Replace dropped paths in `text` with their labels (anything else is left as typed)."""
        found = dropped_paths(text)
        if not found:
            return text
        out, pos = [], 0
        for raw, path in found:
            i = text.find(raw, pos)
            if i < 0:
                continue
            out.append(text[pos:i])
            out.append(self.add(path))
            pos = i + len(raw)
        out.append(text[pos:])
        return "".join(out)

    def used(self, prompt: str) -> List[Attachment]:
        seen = []
        for m in LABEL.finditer(prompt):
            a = self.items.get(int(m.group(2)))
            if a and a not in seen:
                seen.append(a)
        return seen


def encode_image(path: Path) -> str:
    if path.stat().st_size > MAX_IMAGE_BYTES:
        raise ValueError(f"{path.name} is larger than {MAX_IMAGE_BYTES // (1024 * 1024)} MB")
    return base64.b64encode(path.read_bytes()).decode("ascii")


def file_note(a: Attachment) -> str:
    """The content of a dropped (non-image) file, for the prompt."""
    try:
        raw = a.path.read_bytes()
    except OSError as e:
        return f"The user attached {a.label} ({a.path}), but it could not be read: {e}"
    if b"\x00" in raw[:4096]:
        return (f"The user attached {a.label} ({a.path}, {len(raw)} bytes). It is a binary file, "
                "so its content is not shown.")
    text = raw.decode("utf-8", errors="replace").replace("\r\n", "\n")
    if len(text) > MAX_FILE_CHARS:
        text = text[:MAX_FILE_CHARS] + "\n... (truncated)\n"
    return f"The user attached {a.label} ({a.path}):\n```\n{text}\n```"
