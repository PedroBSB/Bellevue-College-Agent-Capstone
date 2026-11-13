"""Project awareness: file tree, BCAGENT.md memory and picking relevant files for the prompt."""
from __future__ import annotations

import os
import platform
import re
from pathlib import Path
from typing import List, Tuple

from .config import HOME

IGNORE_DIRS = {
    ".git", ".hg", ".svn", "node_modules", "__pycache__", ".venv", "venv", "env", ".tox",
    ".mypy_cache", ".pytest_cache", ".ruff_cache", "dist", "build", ".idea", ".vscode",
    ".bcagent", ".ipynb_checkpoints", "site-packages", ".eggs",
}
TEXT_EXT = {
    ".py", ".pyi", ".txt", ".md", ".rst", ".toml", ".cfg", ".ini", ".json", ".yaml", ".yml",
    ".sh", ".bat", ".ps1", ".js", ".ts", ".jsx", ".tsx", ".html", ".css", ".sql", ".csv",
    ".xml", ".env", ".c", ".h", ".cpp", ".java", ".go", ".rs", ".rb", ".php", ".ipynb",
}
CODE_EXT = {".py", ".pyi", ".js", ".ts", ".html", ".css", ".sql", ".sh", ".toml", ".cfg", ".txt", ".md"}
SPECIAL_NAMES = {"Makefile", "Dockerfile", "Procfile", "LICENSE", "README", ".gitignore", ".env.example"}
LANG = {".py": "python", ".ipynb": "python", ".js": "javascript", ".ts": "typescript", ".html": "html", ".css": "css",
        ".json": "json", ".md": "markdown", ".sh": "bash", ".sql": "sql", ".toml": "toml",
        ".yaml": "yaml", ".yml": "yaml", ".ps1": "powershell", ".bat": "bat"}
MEMORY_FILE = "BCAGENT.md"


def notebook_view(raw: str, name: str) -> str:
    """A .ipynb file as Python text with `# %%` cells (what the model reads and writes)."""
    import json
    from .colab import notebook_to_text
    try:
        nb = json.loads(raw)
    except ValueError:
        return raw
    if not isinstance(nb, dict) or "cells" not in nb:
        return raw
    return notebook_to_text(nb, name).replace("# Google Colab notebook:", "# Jupyter notebook:", 1)


def lang_for(path: str) -> str:
    return LANG.get(Path(path).suffix.lower(), "")


class Project:
    def __init__(self, root):
        self.root = Path(root).resolve()
        self.pinned: List[str] = []
        self.truncated: dict = {}   # files shown only partly in the last prompt: {path: (shown, total) lines}
        self.seen: set = set()      # files the model has seen in full (or written) this session

    # -- paths ----------------------------------------------------------------
    def rel(self, p: Path) -> str:
        return Path(p).resolve().relative_to(self.root).as_posix()

    def resolve(self, rel: str) -> Path:
        rel = rel.strip().strip("`'\"").replace("\\", "/")
        if rel.startswith("./"):
            rel = rel[2:]
        p = (self.root / rel).resolve()
        if p != self.root and self.root not in p.parents:
            raise ValueError(f"{rel} is outside the project folder")
        return p

    def exists(self, rel: str) -> bool:
        try:
            return self.resolve(rel).is_file()
        except ValueError:
            return False

    def is_text(self, rel: str) -> bool:
        p = Path(rel)
        return p.suffix.lower() in TEXT_EXT or p.name in SPECIAL_NAMES

    def files(self, limit: int = 400) -> List[str]:
        out: List[str] = []
        for dirpath, dirnames, filenames in os.walk(self.root):
            dirnames[:] = sorted(d for d in dirnames if d not in IGNORE_DIRS
                                 and not d.startswith(".") and not d.endswith(".egg-info"))
            for f in sorted(filenames):
                if f.startswith(".") and f not in SPECIAL_NAMES:
                    continue
                if f.endswith((".pyc", ".pyo")):
                    continue
                out.append(self.rel(Path(dirpath) / f))
                if len(out) >= limit:
                    return out
        return out

    def dirs(self, limit: int = 200) -> List[str]:
        """Project folders (relative, with a trailing slash) for @-completion."""
        seen: List[str] = []
        for f in self.files(limit=2000):
            parts = f.split("/")[:-1]
            for i in range(1, len(parts) + 1):
                d = "/".join(parts[:i]) + "/"
                if d not in seen:
                    seen.append(d)
                    if len(seen) >= limit:
                        return seen
        return seen

    def complete(self, frag: str, limit: int = 40) -> List[str]:
        """Claude-Code-style @ completion: folders and files, best matches first."""
        frag = frag.lower().lstrip("./")
        scored = []
        for path in self.dirs() + self.files(limit=2000):
            low = path.lower()
            name = low.rstrip("/").rsplit("/", 1)[-1]
            if not frag:
                rank = (low.count("/") - low.endswith("/"), 0)
            elif low.startswith(frag):
                rank = (0, len(low))
            elif name.startswith(frag):
                rank = (1, len(low))
            elif frag in name:
                rank = (2, len(low))
            elif frag in low:
                rank = (3, len(low))
            else:
                continue
            scored.append((rank, path))
        scored.sort()
        return [p for _, p in scored[:limit]]

    def read(self, rel: str, max_chars: int = 0) -> str:
        """File content as the model sees it (Jupyter notebooks as `# %%` cells)."""
        text = self.resolve(rel).read_text(encoding="utf-8", errors="replace")
        if rel.lower().endswith(".ipynb"):
            text = notebook_view(text, Path(rel).name)
        if max_chars and len(text) > max_chars:
            text = text[:max_chars] + "\n... (truncated)\n"
        return text

    # -- memory ---------------------------------------------------------------
    def memory(self) -> str:
        parts = []
        for f in (HOME / MEMORY_FILE, self.root / MEMORY_FILE):
            if f.is_file():
                parts.append(f.read_text(encoding="utf-8", errors="replace").strip())
        return "\n\n".join(p for p in parts if p)

    # -- relevance ------------------------------------------------------------
    def mentioned(self, prompt: str) -> List[str]:
        """@path mentions (files or folders)."""
        out: List[str] = []
        for m in re.findall(r"(?:^|(?<=\s))@([\w./\\~+-]+)", prompt):
            m = m.rstrip(".,;:")
            try:
                p = self.resolve(m)
            except ValueError:
                continue
            if p.is_file():
                out.append(self.rel(p))
            elif p.is_dir():
                prefix = self.rel(p)
                out.extend(f for f in self.files() if f.startswith(prefix + "/") and self.is_text(f))
        return out

    def named(self, prompt: str) -> List[str]:
        """Files referenced by name in the prompt (e.g. 'fix calc.py')."""
        files = self.files()
        by_base: dict = {}
        for f in files:
            by_base.setdefault(Path(f).name, []).append(f)
        out: List[str] = []
        for tok in re.findall(r"[\w./\\-]+\.\w+", prompt):
            tok = tok.strip("./")
            if tok in files:
                out.append(tok)
            elif Path(tok).name in by_base and len(by_base[Path(tok).name]) == 1:
                out.append(by_base[Path(tok).name][0])
        return out

    def relevant(self, prompt: str) -> List[str]:
        picked: List[str] = []
        for f in self.pinned + self.mentioned(prompt) + self.named(prompt):
            if f not in picked and self.exists(f):
                picked.append(f)
        code = [f for f in self.files() if Path(f).suffix.lower() in CODE_EXT and Path(f).name != MEMORY_FILE]
        small_project = len(code) <= 6 and sum(self.resolve(f).stat().st_size for f in code) < 14000
        words = set(re.findall(r"[a-z_][a-z0-9_]{2,}", prompt.lower()))
        for f in code:
            if f in picked:
                continue
            if small_project and Path(f).suffix == ".py":
                picked.append(f)
            elif Path(f).stem.lower() in words:
                picked.append(f)
        return picked

    def context(self, prompt: str, budget: int) -> Tuple[str, List[str]]:
        files = self.files(limit=300)
        tree = "\n".join(files[:150]) + (f"\n... and {len(files) - 150} more" if len(files) > 150 else "")
        parts = [
            f"Project folder: {self.root.name}  (OS: {platform.system()})",
            "Files in the project:\n" + (tree or "(empty folder - no files yet)"),
        ]
        mem = self.memory()
        if mem:
            parts.append("Project notes (BCAGENT.md):\n" + mem[:3000])
        used = sum(len(p) for p in parts)
        included: List[str] = []
        self.truncated = {}
        for f in self.relevant(prompt):
            if not self.is_text(f):
                continue
            try:
                full = self.read(f)
            except OSError:
                continue
            limit = max(1500, budget - used - 200)
            if len(full) > limit:
                body = full[:limit] + "\n... (truncated)\n"
                self.truncated[f] = (full[:limit].count("\n"), full.count("\n") + 1)
            else:
                body = full
                self.seen.add(f)
            block = f"FILE: {f}\n```{lang_for(f)}\n{body.rstrip()}\n```"
            if used + len(block) > budget and included:
                break
            parts.append(block)
            included.append(f)
            used += len(block)
        if included:
            parts.insert(2, "Current content of the relevant files is shown below.")
        return "\n\n".join(parts), included
