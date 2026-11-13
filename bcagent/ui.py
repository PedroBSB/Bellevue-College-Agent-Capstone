"""Look & feel: Bellevue College Blue theme, Bulldog banner, diffs and prompts."""
from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import List, Optional, Tuple

from rich.console import Console, Group
from rich.markdown import Markdown
from rich.markup import escape
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

# The multicolor Bulldog drawing (each character is one color). It is shrunk into solid
# half-block pixels for the terminal by logo_pixels().
LOGO = '''
          ####                             X######=
        #OOO#OOX                        =#O###OOOOO#
      XOO#== @OOX                      #OO#@==XXOOOOO
     XOO#==XOX@#O#X    =XX=X====X=X  XOO#@@==== #OOOOOOX
     OO===@OOO#XX#O@X#XOOOOOOOOOOOO##XXX===#X=#=@#OOOOOOX
    #OX=X XOOOOO##O#OOOOO#XXXXXXXXXXOO#X@==#XXX#=XOOOOOOO#
   XO#=#=@#OOOOOO###=X##OOOOOOOOOOO#XOOOOX@==#X===#OOOOOOOX
   O#=XX=@OOOOO##X##OOOOOOOOOOOOOOOOOOOOOWOX==X===@#OOOO###
   O#=X==XOOO#=X#OO####OOOOOOOOOOOOOOOO####OX=X=== @#OOO@XX
   ####@#OOO#=XOOX@XXXX@#OOOOOOOOOOOOOX@#X@@#@=X=X= @OOO@
    ##X ##O#=#O#@@#OOOOOXXOOOOOOOOOO#@#OOOOXXO=X  X==@OO=
         #OX=##@#OO#XXXOOOOOOOOOOOOOO#OO#XOOXX=X   X= ##
        #OX#OO@#W#@XOX@@#OOOOOOOOOOOOO#=XOX#O@X     =XX
        O##OO#XO#@OW#@##@#OOOOOOOOOOO#X#X#WX#WX
       #OOOOO#OO@OWO@@#WX@OOOO######OX#W@@OWX#O=
       #OOOOOOOX@WWO@= @=XO#XXXXXXXXOX@@@@OW##O=
        OOO#XXX@@OOOX@=  X###O#XXXXXX#@  #OW##O#X
        ##@XX##O#XXX##@@###O#@=======X#X@XXXXX#OO#
       O#@XOOOOOOOOOX@@#XX#O@ =@@@@@X=@#OXXXOOOOOO#
      OXXOOOOO##X###OO#X#OOOX@@@ === @XOOO##XX#OOOO#
     X#@OOOOOXX##OOOOOOOOOOOOO#X@ = @#OOOOOOO#X#OOOOX
     #XOOOOOX#OOO#@#OOOOOOOOOOOO##XXOOOOOOXX#OO@#OOO#
    #O#OOOO##OO#@@#OOOOOOOOOOOOOOO##OOOOOOOOXXO#@OO#OX
    ##OOOOO#OOO##XOOOOOOO#OOOOOOO#X#OOOOO#XO#@O#@OO##X
    ###OOOOOOOOOO#@OOOO#X#XOOOOO#==@#OOOX#OX@#OXOOO##X
     #XOOOOOOOOOOOX@OOO@OW#X###===   =XXXWO@#O#XOO#=#
     ###OOOOX#OOOOOX@#XXWWW@= @++++++@@XWWOXOOX#O#=#X
      #=#OOO#XOOOOOO#X #OWWO@+++++++++@X##X#OOXOO#=#
      =#=#OOO@XOOOOOOO#X@XXX@+@X###########OO#XO#=X=
        ==X#O#@XOOOOOOOOOOOOOOOOO##O#####OOO#@##===X#=
      XOO@ =#OO@@OOOOOOOOOOO#####=X==###OOO#@##=X #WWOOX
    XWWWWX =X###@@#OOOO#OOOOOOOOOOOOOOOOOO#=X#=@  OWWWWO#@
    XX#OOX   ===#X @XXOXOOOOOOOOOOOO##OOOX==XX=   OWOXX=
           ==   XX====@=@#OOOOOOOO#=#OO#@=X=
              =    ==XX=====#####===#X@  =  ==
           #W#  ==      XXXXX=X=XX@       ==  =OO
          #WWWO = =====             =======  #WWWO
         #WOO#=        ==============   =    X#OOW#
         ##       #WW#X = =       ==  #OWO       ##
                  #WWWX    @OOOO#    @OWWO
                  #WO@      #WWWX      #WO
                  ##        @OW#        X#
                             #WX
                              #
'''.strip("\n")
LOGO_COLORS = {
    "#": "#6E7678",   # grey fur
    "O": "#B5B7B6",   # light fur
    "X": "#333D45",   # dark shading
    "=": "#075092",   # Bellevue College blue
    "@": "#0B0E12",   # outline
    "W": "#FAFAFA",   # white highlights
    "+": "#D85549",   # tongue
}
LOGO_WIDTH = 28   # columns of the solid logo in the terminal
LOGO_LINES = 16   # terminal lines (each line holds two pixel rows: ▀ top, ▄ bottom)


def logo_pixels(width: int = LOGO_WIDTH, lines: int = LOGO_LINES) -> List[List[Optional[str]]]:
    """Shrink the character drawing into a grid of hex colors (None = transparent).

    Gaps inside the dog are filled first, so the result is solid; each pixel is the average color
    of the characters it covers, except that the eyes, teeth and tongue are kept crisp."""
    rows = LOGO.splitlines()
    h, w = len(rows), max(len(r) for r in rows)
    grid = [list(r.ljust(w)) for r in rows]
    outside = [[False] * w for _ in range(h)]
    stack = [(y, x) for y in range(h) for x in (0, w - 1)] + [(y, x) for x in range(w) for y in (0, h - 1)]
    while stack:
        y, x = stack.pop()
        if 0 <= y < h and 0 <= x < w and not outside[y][x] and grid[y][x] == " ":
            outside[y][x] = True
            stack += [(y + 1, x), (y - 1, x), (y, x + 1), (y, x - 1)]
    for _ in range(6):  # fill holes inside the silhouette from their neighbours
        for y in range(h):
            for x in range(w):
                if grid[y][x] == " " and not outside[y][x]:
                    for dy, dx in ((0, -1), (0, 1), (-1, 0), (1, 0)):
                        yy, xx = y + dy, x + dx
                        if 0 <= yy < h and 0 <= xx < w and grid[yy][xx] != " ":
                            grid[y][x] = grid[yy][xx]
                            break
    rgb = {k: tuple(int(v[i:i + 2], 16) for i in (1, 3, 5)) for k, v in LOGO_COLORS.items()}
    ph = lines * 2
    out: List[List[Optional[str]]] = []
    for py in range(ph):
        row: List[Optional[str]] = []
        y0 = py * h // ph
        y1 = max(y0 + 1, (py + 1) * h // ph)
        for px in range(width):
            x0 = px * w // width
            x1 = max(x0 + 1, (px + 1) * w // width)
            cells = [grid[y][x] for y in range(y0, y1) for x in range(x0, x1)]
            solid = [c for c in cells if c in rgb]
            if len(solid) < len(cells) / 2:
                row.append(None)
                continue
            keep = next((k for k in "+W" if cells.count(k) * 3 >= len(cells)), None)
            if keep:
                row.append(LOGO_COLORS[keep])
                continue
            r, g, b = (sum(rgb[c][i] for c in solid) // len(solid) for i in range(3))
            row.append(f"#{r:02X}{g:02X}{b:02X}")
        out.append(row)
    return out


def logo_text() -> Text:
    """The solid multicolor Bulldog as rich Text, two pixels per character cell."""
    px = logo_pixels()
    art = Text()
    for y in range(0, len(px), 2):
        for top, bottom in zip(px[y], px[y + 1]):
            if top and bottom:
                art.append("▀", style=f"{top} on {bottom}")
            elif top:
                art.append("▀", style=top)
            elif bottom:
                art.append("▄", style=bottom)
            else:
                art.append(" ")
        art.append("\n")
    return art



TIPS = [
    "Ask for code: [bc]write a script that converts csv to json[/]",
    "Mention files with [bc]@file.py[/] to give bcagent context",
    "Run [bc]/init[/] to create a BCAGENT.md with notes about your project",
    "[bc]shift+tab[/] auto-accepts edits · [bc]![/]cmd runs a shell command",
    "Just say [bc]commit and push[/] or [bc]run the tests[/] · [bc]/learn[/] for hints instead of answers",
    "numpy, pandas, scikit-learn, statsmodels and statsformula are ready to use",
]


def bulldog_badge() -> Panel:
    """The solid multicolor Bulldog with BELLEVUE COLLEGE under it, in a small panel."""
    art = logo_text()
    art.append("\n")
    art.append("BELLEVUE COLLEGE".center(LOGO_WIDTH), style=f"bold {BC_BLUE_LIGHT}")
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


CHAT = -1                      # ask() result when the user picks "Chat about this"
CHAT_LABEL = "Chat about this"


def ask(question: str, options: List[str], cancel: Optional[int] = None,
        chat: bool = True) -> Tuple[int, Optional[str]]:
    """Claude-Code-style menu: move with the up/down arrows (or j/k) and press enter, or press a number.

    With chat=True a final "Chat about this" option lets the user type a message instead (to clarify or
    change the request); then the result is (CHAT, message). Otherwise it is (index, None).
    `cancel` is the option chosen by esc / "n" (default: the last real option).
    """
    cancel = len(options) - 1 if cancel is None else cancel
    shown = list(options) + ([CHAT_LABEL] if chat else [])
    console.print(f"[bold]{question}[/]")
    while True:
        idx = None
        if is_interactive() and not os.environ.get("BCAGENT_PLAIN_MENU"):
            try:
                idx = _arrow_menu([Text.from_markup(o).plain for o in shown], cancel)
            except (KeyboardInterrupt, EOFError):
                raise
            except Exception:  # odd terminal (old Windows console, no tty...) -> typed answers
                os.environ["BCAGENT_PLAIN_MENU"] = "1"
        if idx is None:
            idx, msg = _typed_menu(shown, cancel, chat)
            if msg:
                return CHAT, msg
        if chat and idx == len(options):
            msg = _chat_input()
            if msg:
                return CHAT, msg
            continue  # empty message -> show the menu again
        console.print(f"  [muted]⎿  {escape(Text.from_markup(shown[idx]).plain)}[/]")
        return idx, None


def choose(question: str, options: List[str], cancel: Optional[int] = None) -> int:
    """Menu without the chat option. Returns the chosen index (0-based)."""
    return ask(question, options, cancel, chat=False)[0]


def _chat_input() -> str:
    console.print("  [muted]Tell bcagent what you mean or what to change (enter on an empty line = back "
                  "to the options)[/]")
    try:
        return console.input("  [bc]❯[/] ").strip()
    except EOFError:
        return ""


def _arrow_menu(labels: List[str], cancel: int) -> int:
    from prompt_toolkit.application import Application
    from prompt_toolkit.key_binding import KeyBindings
    from prompt_toolkit.layout import Layout
    from prompt_toolkit.layout.containers import Window
    from prompt_toolkit.layout.controls import FormattedTextControl
    from prompt_toolkit.styles import Style

    state = {"i": 0}
    n = len(labels)

    def render():
        out = []
        for k, label in enumerate(labels):
            sel = k == state["i"]
            out.append(("class:sel" if sel else "", f"{'❯' if sel else ' '} {k + 1}. {label}\n"))
        out.append(("class:hint", f"  ↑/↓ to move · enter to select · 1-{n} shortcut · esc to cancel"))
        return out

    kb = KeyBindings()

    @kb.add("up")
    @kb.add("k")
    @kb.add("s-tab")
    def _up(event):
        state["i"] = (state["i"] - 1) % n

    @kb.add("down")
    @kb.add("j")
    @kb.add("tab")
    def _down(event):
        state["i"] = (state["i"] + 1) % n

    @kb.add("enter")
    def _enter(event):
        event.app.exit(result=state["i"])

    def pick(k):
        return lambda event: event.app.exit(result=k)

    for k in range(min(n, 9)):
        kb.add(str(k + 1))(pick(k))
    kb.add("y")(pick(0))
    kb.add("n")(pick(cancel))
    kb.add("escape", eager=True)(pick(cancel))
    kb.add("q")(pick(cancel))

    @kb.add("c-c")
    def _ctrl_c(event):
        event.app.exit(exception=KeyboardInterrupt)

    @kb.add("c-d")
    def _ctrl_d(event):
        event.app.exit(exception=EOFError)

    # No cursor-position requests: terminals that don't answer them would swallow the next keys typed.
    os.environ.setdefault("PROMPT_TOOLKIT_NO_CPR", "1")
    style = Style.from_dict({"sel": f"bold {BC_BLUE_LIGHT}", "hint": "#8a8a8a"})
    app = Application(layout=Layout(Window(FormattedTextControl(render, show_cursor=False),
                                           dont_extend_height=True)),
                      key_bindings=kb, style=style, full_screen=False, erase_when_done=True,
                      mouse_support=False)
    return app.run()


def _typed_menu(labels: List[str], cancel: int, chat: bool) -> Tuple[int, Optional[str]]:
    """Fallback when arrow keys can't be used: type a number (or y/n). With chat on, any other text
    is sent to bcagent as a message."""
    for k, label in enumerate(labels, 1):
        console.print(f"  [bc]{k}.[/] {label}")
    while True:
        try:
            ans = console.input("[bc]❯[/] ").strip()
        except EOFError:
            return cancel, None
        low = ans.lower()
        if low.isdigit() and 1 <= int(low) <= len(labels):
            return int(low) - 1, None
        if low in ("y", "yes", ""):
            return 0, None
        if low in ("n", "no", "q"):
            return cancel, None
        if low in ("a", "always") and cancel >= 2:
            return 1, None
        if chat and low in ("c", "chat"):
            return len(labels) - 1, None
        if chat:
            return CHAT, ans


def is_interactive() -> bool:
    return sys.stdin.isatty() and sys.stdout.isatty() and os.environ.get("TERM") != "dumb"
