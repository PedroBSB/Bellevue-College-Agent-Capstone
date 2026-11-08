"""Look & feel: Bellevue College Blue theme, Bulldog banner, diffs and prompts."""
from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import List

from rich.console import Console, Group
from rich.markdown import Markdown
from rich.panel import Panel
from rich.syntax import Syntax
from rich.table import Table
from rich.text import Text
from rich.theme import Theme

from . import __version__

BC_BLUE = "#003D79"        # Bellevue College Blue
BC_BLUE_LIGHT = "#4F8FDB"  # lighter tint of BC Blue, readable on dark terminals
BC_WHITE = "#FFFFFF"

THEME = Theme({
    "bc": f"bold {BC_BLUE_LIGHT}",
    "bcblue": f"bold {BC_BLUE}",
    "muted": "grey58",
    "ok": "bold green3",
    "err": "bold red",
    "warn": "bold yellow",
    "add": "green3",
    "del": "red3",
})

console = Console(theme=THEME, highlight=False)
err_console = Console(theme=THEME, highlight=False, stderr=True)

BULLDOG = r"""
   .-"-.            .-"-.
  / .-. \__________/ .-. \
  \ \  /            \  / /
   \_\/  .--.  .--.  \/_/
     |   | o|  |o |   |
     |   '--'  '--'   |
     |     .----.     |
    /|    ( (__) )    |\
   | |  .--'----'--.  | |
   | | (  \/\/\/\/  ) | |
    \ \ '-.______.-' / /
     '-.____________.-'
"""

TIPS = [
    "Ask for code: [bc]write a script that converts csv to json[/]",
    "Mention files with [bc]@file.py[/] to give bcagent context",
    "Run [bc]/init[/] to create a BCAGENT.md with notes about your project",
    "[bc]shift+tab[/] auto-accepts edits · [bc]![/]cmd runs a shell command",
    "Install more agents: [bc]/agents catalog[/] then [bc]/install bulldog-1.5b[/]",
]


def bulldog_badge() -> Panel:
    """The Bulldog logo, drawn entirely in Bellevue College blue."""
    art = Text(BULLDOG.strip("\n"), style=f"bold {BC_BLUE_LIGHT}")
    art.append("\n\n")
    width = max(len(line) for line in BULLDOG.strip("\n").splitlines())
    art.append("BELLEVUE COLLEGE".center(width), style=f"bold {BC_BLUE_LIGHT}")
    return Panel(art, border_style=BC_BLUE_LIGHT, padding=(0, 1), expand=False)


def shorten(path: Path) -> str:
    home = str(Path.home())
    s = str(path)
    return "~" + s[len(home):] if s.startswith(home) else s


def welcome(cwd: Path, agent_name: str, model: str) -> None:
    info = Text()
    info.append("✻ ", style="bc")
    info.append("Welcome to bcagent!", style="bold")
    info.append(f"  v{__version__}\n", style="muted")
    info.append("Bellevue College Agent · local Python coder\n\n", style="bc")
    info.append("/help", style="bc")
    info.append(" for help, ", style="muted")
    info.append("/agents", style="bc")
    info.append(" to switch agents\n\n", style="muted")
    info.append("cwd:   ", style="muted")
    info.append(shorten(cwd) + "\n")
    info.append("agent: ", style="muted")
    info.append(agent_name, style="bc")
    info.append(f"  ({model}, runs 100% locally)\n", style="muted")
    tips = Text.from_markup("\n[muted]Tips for getting started:[/]\n" +
                            "\n".join(f" {i}. {t}" for i, t in enumerate(TIPS, 1)))
    if console.width >= 88:
        grid = Table.grid(padding=(0, 3))
        grid.add_column()
        grid.add_column(vertical="middle")
        grid.add_row(bulldog_badge(), Group(info, tips))
        body = grid
    else:
        body = Group(bulldog_badge(), info, tips)
    console.print(Panel(body, border_style=BC_BLUE_LIGHT, padding=(1, 2),
                        title=f"[bold {BC_WHITE} on {BC_BLUE}] bcagent [/]", title_align="left"))


def assistant(text: str) -> None:
    grid = Table.grid(padding=(0, 1))
    grid.add_column(width=1, style="bc")
    grid.add_column(ratio=1)
    grid.add_row("●", Markdown(text or "_(no answer)_", code_theme="monokai"))
    console.print(grid)


def show_edit(edit) -> None:
    from .project import lang_for
    title = f"[bold]{'Create' if edit.is_new else 'Update'}[/] [bc]{edit.path}[/]"
    if edit.is_new:
        lines = edit.new.splitlines()
        shown = "\n".join(lines[:60])
        body = Syntax(shown or " ", lang_for(edit.path) or "text", theme="monokai",
                      line_numbers=True, word_wrap=True)
        extra = f"\n[muted]… {len(lines) - 60} more lines[/]" if len(lines) > 60 else ""
        content = Group(body, Text.from_markup(extra)) if extra else body
    else:
        out = Text()
        diff = edit.diff_lines()[2:]
        for line in diff[:150]:
            if line.startswith("+"):
                out.append(line + "\n", style="add")
            elif line.startswith("-"):
                out.append(line + "\n", style="del")
            elif line.startswith("@@"):
                out.append(line + "\n", style="bc")
            else:
                out.append(line + "\n", style="muted")
        if len(diff) > 150:
            out.append(f"… {len(diff) - 150} more diff lines\n", style="muted")
        content = out
    a, r = edit.stats()
    console.print(Panel(content, title=title, title_align="left", border_style=BC_BLUE_LIGHT,
                        subtitle=f"[add]+{a}[/] [del]-{r}[/]", subtitle_align="right"))
    if edit.warning:
        console.print(f"  [warn]⚠ {edit.warning}[/]")


def choose(question: str, options: List[str]) -> int:
    """Claude-Code-style numbered menu. Returns the chosen index (0-based). Esc/empty -> last option."""
    console.print(f"[bold]{question}[/]")
    for i, opt in enumerate(options, 1):
        console.print(f"  [bc]{i}.[/] {opt}")
    while True:
        try:
            ans = console.input("[bc]❯[/] ").strip().lower()
        except EOFError:
            return len(options) - 1
        if ans.isdigit() and 1 <= int(ans) <= len(options):
            return int(ans) - 1
        if ans in ("y", "yes", ""):
            return 0
        if ans in ("n", "no", "q"):
            return len(options) - 1
        if ans in ("a", "always") and len(options) > 2:
            return 1


def is_interactive() -> bool:
    return sys.stdin.isatty() and sys.stdout.isatty() and os.environ.get("TERM") != "dumb"
