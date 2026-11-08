"""Command line entry point: `bcagent`."""
from __future__ import annotations

import argparse
import os
import platform
import sys
from pathlib import Path
from typing import List, Optional

from rich.progress import BarColumn, DownloadColumn, Progress, TextColumn, TransferSpeedColumn
from rich.table import Table

from . import __version__, agents as agents_mod, ui
from .agents import CATALOG, Agent, all_agents
from .config import AGENTS_DIR, CONFIG_FILE, HISTORY_FILE, load_config, save_config
from .llm import Ollama, OllamaError
from .project import MEMORY_FILE, Project
from .session import Session, run_command
from .ui import console

COMMANDS = [
    ("/help", "Show help and shortcuts"),
    ("/agents", "List agents  (/agents catalog = installable agents)"),
    ("/agent", "Switch agent: /agent <name>"),
    ("/install", "Install an agent: /install <catalog-name | ollama-model | file.md | url>"),
    ("/model", "Use another Ollama model for this session: /model <tag>"),
    ("/add", "Always include a file in context: /add <file>"),
    ("/drop", "Stop including a file: /drop <file> (or /drop all)"),
    ("/files", "Show files in context"),
    ("/init", f"Create {MEMORY_FILE} with notes about this project"),
    ("/undo", "Undo the last set of file edits"),
    ("/auto", "Toggle auto-accept of edits (same as shift+tab)"),
    ("/clear", "Clear conversation history"),
    ("/status", "Show agent, model and Ollama status"),
    ("/doctor", "Check your installation"),
    ("/exit", "Exit bcagent"),
]

HELP = """[bold]bcagent[/] – Bellevue College Agent. Type what you want and press enter.

[bold]Examples[/]
  [bc]create a tic-tac-toe game in tictactoe.py[/]
  [bc]add input validation to @calc.py[/]
  [bc]write pytest tests for @utils.py[/]

[bold]Shortcuts[/]
  [bc]@file[/]        include a file or folder in the context
  [bc]!command[/]     run a shell command (output is shared with bcagent)
  [bc]shift+tab[/]    toggle auto-accept edits
  [bc]esc enter[/]    new line ·  [bc]ctrl+c[/] interrupt ·  [bc]ctrl+d[/] exit
"""


# -- model / server helpers ----------------------------------------------------

def ensure_model(client: Ollama, model: str) -> bool:
    if not client.ensure_running():
        console.print("[err]✗ Ollama is not running and could not be started.[/]\n"
                      "  Install it from [bc]https://ollama.com/download[/] or re-run the bcagent installer.")
        return False
    try:
        if client.has_model(model):
            return True
        console.print(f"[bc]⬇[/] Downloading model [bold]{model}[/] (first time only)…")
        with Progress(TextColumn("  {task.description}"), BarColumn(complete_style=ui.BC_BLUE_LIGHT),
                      DownloadColumn(), TransferSpeedColumn(), console=console, transient=True) as prog:
            task = prog.add_task("pulling", total=None)

            def on(ev):
                prog.update(task, description=ev.get("status", "")[:40],
                            total=ev.get("total"), completed=ev.get("completed", 0))
            client.pull(model, on)
        console.print(f"[ok]✓[/] {model} ready")
        return True
    except OllamaError as e:
        console.print(f"[err]✗ {e}[/]")
        return False


def doctor(client: Ollama, cfg: dict) -> None:
    t = Table.grid(padding=(0, 2))
    t.add_column(style="muted")
    t.add_column()
    ok, bad = "[ok]✓[/]", "[err]✗[/]"
    t.add_row("bcagent", f"{ok} v{__version__}")
    t.add_row("python", f"{ok} {platform.python_version()} ({sys.executable})")
    t.add_row("system", f"{platform.system()} {platform.release()} {platform.machine()}")
    exe = client.binary()
    t.add_row("ollama binary", f"{ok} {exe}" if exe else f"{bad} not found – https://ollama.com/download")
    ver = client.version()
    t.add_row("ollama server", f"{ok} {client.host} (v{ver})" if ver else f"{bad} not running at {client.host}")
    agent = all_agents(Path.cwd()).get(cfg.get("agent", "bulldog"))
    if agent:
        have = False
        if ver:
            try:
                have = client.has_model(agent.model)
            except OllamaError:
                pass
        t.add_row("agent", f"{agent.name} → {agent.model} " + (ok if have else f"{bad} model not downloaded"))
    t.add_row("config", str(CONFIG_FILE))
    t.add_row("agents dir", str(AGENTS_DIR))
    console.print(t)


def print_agents(project_root: Optional[Path], current: str, catalog: bool = False) -> None:
    t = Table(border_style=ui.BC_BLUE_LIGHT, header_style="bc", show_lines=False)
    t.add_column("")
    t.add_column("agent")
    t.add_column("model")
    t.add_column("description")
    if catalog:
        installed = all_agents(project_root)
        for a in CATALOG.values():
            t.add_row("✓" if a.name in installed else "", a.name, a.model, a.description)
        console.print(t)
        console.print("[muted]Install with[/] [bc]bcagent agents install <name>[/] [muted]– any Ollama model tag, "
                      "a .md agent file or a URL to one also works.[/]")
        return
    for a in all_agents(project_root).values():
        t.add_row("[bc]●[/]" if a.name == current else "", a.name, a.model, a.description)
    console.print(t)


def install_agent(client: Ollama, spec: str, **kw) -> Optional[Agent]:
    try:
        agent = agents_mod.install(spec, **kw)
    except Exception as e:  # network / file errors
        console.print(f"[err]✗ could not install {spec}: {e}[/]")
        return None
    console.print(f"[ok]✓[/] installed agent [bc]{agent.name}[/] ({agent.model})")
    ensure_model(client, agent.model)
    return agent


def agents_command(argv: List[str], client: Ollama, cfg: dict) -> int:
    p = argparse.ArgumentParser(prog="bcagent agents", description="Manage bcagent agents")
    sub = p.add_subparsers(dest="cmd")
    sub.add_parser("list", help="list installed agents")
    sub.add_parser("catalog", help="list agents you can install")
    pi = sub.add_parser("install", help="install from catalog name, Ollama model tag, .md file or URL")
    pi.add_argument("spec")
    pi.add_argument("--name")
    pi.add_argument("--model")
    pi.add_argument("--prompt", dest="instructions", help="extra instructions for the agent")
    pi.add_argument("--format", dest="edit_format", choices=["whole", "diff", "none"])
    pi.add_argument("--description")
    pr = sub.add_parser("remove", help="remove an installed agent")
    pr.add_argument("name")
    pr.add_argument("--delete-model", action="store_true", help="also delete the Ollama model")
    pu = sub.add_parser("use", help="set the default agent")
    pu.add_argument("name")
    pc = sub.add_parser("create", help="create a custom agent markdown file to edit")
    pc.add_argument("name")
    pc.add_argument("--model", default=None)
    a = p.parse_args(argv)
    root = Path.cwd()
    if a.cmd in (None, "list"):
        print_agents(root, cfg.get("agent", "bulldog"))
    elif a.cmd == "catalog":
        print_agents(root, cfg.get("agent", "bulldog"), catalog=True)
    elif a.cmd == "install":
        kw = {k: getattr(a, k) for k in ("name", "model", "instructions", "edit_format", "description")}
        return 0 if install_agent(client, a.spec, **kw) else 1
    elif a.cmd == "remove":
        agent = all_agents(root).get(a.name)
        try:
            where = agents_mod.remove(a.name)
        except ValueError as e:
            console.print(f"[err]✗ {e}[/]")
            return 1
        console.print(f"[ok]✓[/] removed {a.name} ({where})")
        if a.delete_model and agent:
            try:
                client.delete(agent.model)
                console.print(f"[ok]✓[/] deleted model {agent.model}")
            except OllamaError as e:
                console.print(f"[warn]{e}[/]")
    elif a.cmd == "use":
        if a.name not in all_agents(root):
            console.print(f"[err]✗ unknown agent {a.name}[/] – see [bc]bcagent agents list[/]")
            return 1
        cfg["agent"] = a.name
        save_config(cfg)
        console.print(f"[ok]✓[/] default agent is now [bc]{a.name}[/]")
    elif a.cmd == "create":
        path = agents_mod.create_template(a.name, a.model or agents_mod.DEFAULT_MODEL)
        console.print(f"[ok]✓[/] edit your agent here: [bc]{path}[/]\n  then run [bc]bcagent -a {a.name}[/]")
    return 0


# -- interactive REPL -----------------------------------------------------------

def make_prompt(session: Session):
    from prompt_toolkit import PromptSession
    from prompt_toolkit.completion import Completer, Completion
    from prompt_toolkit.formatted_text import HTML
    from prompt_toolkit.history import FileHistory
    from prompt_toolkit.key_binding import KeyBindings
    from prompt_toolkit.styles import Style

    class BCCompleter(Completer):
        def get_completions(self, document, complete_event):
            text = document.text_before_cursor
            if text.startswith("/") and " " not in text:
                for name, desc in COMMANDS:
                    if name.startswith(text):
                        yield Completion(name, start_position=-len(text), display_meta=desc)
                return
            word = document.get_word_before_cursor(WORD=True)
            if word.startswith("@"):
                frag = word[1:].lower()
                n = 0
                for f in session.project.files():
                    if frag in f.lower():
                        yield Completion("@" + f, start_position=-len(word))
                        n += 1
                        if n >= 40:
                            return

    kb = KeyBindings()

    @kb.add("s-tab")
    def _(event):
        session.auto = not session.auto
        event.app.invalidate()

    @kb.add("escape", "enter")
    def _(event):
        event.current_buffer.insert_text("\n")

    def toolbar():
        left = ("<b><style fg='ansigreen'>⏵⏵ accept edits on</style></b> (shift+tab to toggle)"
                if session.auto else "? for shortcuts")
        return HTML(f" {left}   <style fg='{ui.BC_BLUE_LIGHT}'>{session.agent.name}</style>"
                    f" · {session.agent.model}")

    style = Style.from_dict({"prompt": f"bold {ui.BC_BLUE_LIGHT}", "bottom-toolbar": "noreverse #8a8a8a",
                             "completion-menu.completion.current": f"bg:{ui.BC_BLUE} #ffffff"})
    HISTORY_FILE.parent.mkdir(parents=True, exist_ok=True)
    ps = PromptSession(history=FileHistory(str(HISTORY_FILE)), completer=BCCompleter(),
                       complete_while_typing=True, key_bindings=kb, bottom_toolbar=toolbar, style=style)
    return lambda: ps.prompt([("class:prompt", "> ")])


def init_memory(project: Project) -> None:
    path = project.root / MEMORY_FILE
    if path.exists():
        console.print(f"[muted]{MEMORY_FILE} already exists – edit it to change bcagent's project notes.[/]")
        return
    files = project.files(limit=60)
    path.write_text(
        f"# {project.root.name}\n\n"
        "Notes for bcagent (the Bellevue College Agent). This file is sent with every prompt.\n\n"
        "## About this project\n- Describe what the project does here.\n\n"
        "## Conventions\n- Python 3, follow PEP 8, use type hints and docstrings.\n"
        "- Put tests in tests/ and run them with `python -m pytest`.\n\n"
        "## Files\n" + "\n".join(f"- {f}" for f in files) + "\n", encoding="utf-8")
    console.print(f"[ok]✓[/] created [bc]{MEMORY_FILE}[/] – edit it to teach bcagent about your project.")


def handle_slash(line: str, session: Session, client: Ollama, cfg: dict) -> bool:
    """Returns False when the REPL should exit."""
    cmd, _, arg = line.partition(" ")
    arg = arg.strip()
    proj = session.project
    if cmd in ("/exit", "/quit", "/q"):
        return False
    if cmd in ("/help", "/?"):
        console.print(HELP)
        t = Table.grid(padding=(0, 2))
        for name, desc in COMMANDS:
            t.add_row(f"[bc]{name}[/]", desc)
        console.print(t)
    elif cmd == "/clear":
        session.history.clear()
        console.clear()
        console.print("[muted]Conversation cleared.[/]")
    elif cmd == "/agents":
        print_agents(proj.root, session.agent.name, catalog=(arg == "catalog"))
    elif cmd == "/agent":
        found = all_agents(proj.root).get(arg)
        if not found:
            console.print(f"[err]✗ unknown agent '{arg}'[/] – try [bc]/agents[/] or [bc]/agents catalog[/]")
        elif ensure_model(client, found.model):
            session.agent = found
            console.print(f"[ok]✓[/] now using [bc]{found.name}[/] ({found.model})")
    elif cmd == "/install":
        if not arg:
            print_agents(proj.root, session.agent.name, catalog=True)
        else:
            a = install_agent(client, arg)
            if a and ui.choose(f"Switch to {a.name} now?", ["Yes", "No"]) == 0:
                session.agent = a
    elif cmd == "/model":
        if not arg:
            try:
                console.print("Installed models: " + ", ".join(client.models()))
            except OllamaError as e:
                console.print(f"[err]{e}[/]")
        elif ensure_model(client, arg):
            session.agent = Agent(session.agent.name, arg, session.agent.description, session.agent.instructions,
                                  session.agent.edit_format, session.agent.temperature, "session")
            console.print(f"[ok]✓[/] using model [bc]{arg}[/] for this session")
    elif cmd == "/add":
        for f in arg.split():
            f = f.lstrip("@")
            if proj.exists(f):
                rel = proj.rel(proj.resolve(f))
                if rel not in proj.pinned:
                    proj.pinned.append(rel)
                console.print(f"[ok]✓[/] added {rel}")
            else:
                console.print(f"[err]✗ no such file: {f}[/]")
    elif cmd == "/drop":
        if arg in ("", "all"):
            proj.pinned.clear()
        else:
            proj.pinned = [p for p in proj.pinned if p not in arg.split()]
        console.print("[ok]✓[/] context: " + (", ".join(proj.pinned) or "(no pinned files)"))
    elif cmd == "/files":
        console.print("pinned: " + (", ".join(proj.pinned) or "(none)"))
        console.print("[muted]bcagent also adds files you @mention or name, and all .py files in small projects.[/]")
    elif cmd == "/init":
        init_memory(proj)
    elif cmd == "/undo":
        session.undo()
    elif cmd == "/auto":
        session.auto = not session.auto
        console.print(f"auto-accept edits: {'[ok]on[/]' if session.auto else 'off'}")
    elif cmd == "/status":
        console.print(f"agent [bc]{session.agent.name}[/] · model {session.agent.model} · "
                      f"ollama {client.host} v{client.version()} · cwd {proj.root}")
    elif cmd == "/doctor":
        doctor(client, cfg)
    else:
        console.print(f"[err]Unknown command {cmd}[/] – type [bc]/help[/]")
    return True


def repl(session: Session, client: Ollama, cfg: dict, first: Optional[str]) -> None:
    ui.welcome(session.project.root, session.agent.name, session.agent.model)
    read = make_prompt(session) if ui.is_interactive() else (lambda: input("> "))
    pending = first
    while True:
        try:
            line = pending if pending is not None else read()
            pending = None
        except KeyboardInterrupt:
            continue
        except EOFError:
            break
        line = (line or "").strip()
        if not line:
            continue
        try:
            if line == "?":
                console.print(HELP)
            elif line.startswith("/"):
                if not handle_slash(line, session, client, cfg):
                    break
            elif line.startswith("!"):
                code, out = run_command(line[1:].strip(), str(session.project.root))
                session.notes.append(f"I ran `{line[1:].strip()}` (exit code {code}). Output:\n```\n"
                                     f"{out[-2500:]}\n```")
            else:
                session.turn(line)
        except KeyboardInterrupt:
            console.print("  [warn]⎿  Interrupted[/]")
        except OllamaError as e:
            console.print(f"[err]✗ {e}[/]")
        console.print()
    console.print("[bc]Go Bulldogs! 🐾[/]")


# -- main ------------------------------------------------------------------------

def main(argv: Optional[List[str]] = None) -> None:
    argv = list(sys.argv[1:] if argv is None else argv)
    if os.name == "nt":
        os.system("")  # enable ANSI colors on old Windows consoles
    cfg = load_config()
    client = Ollama(cfg.get("ollama_host"))

    if argv and argv[0] == "agents":
        sys.exit(agents_command(argv[1:], client, cfg))
    if argv and argv[0] == "doctor":
        doctor(client, cfg)
        return

    p = argparse.ArgumentParser(
        prog="bcagent",
        description="bcagent – Bellevue College Agent: a local AI coding CLI (like Claude Code) "
                    "powered by the smallest, fastest local code LLM.",
        epilog="Other commands: `bcagent agents [list|catalog|install|remove|use|create]`, `bcagent doctor`")
    p.add_argument("prompt", nargs="*", help="initial prompt")
    p.add_argument("-p", "--print", action="store_true", dest="print_mode",
                   help="non-interactive: answer the prompt, print the result and exit")
    p.add_argument("-y", "--yes", action="store_true", help="auto-accept file edits")
    p.add_argument("-a", "--agent", help="agent to use (see `bcagent agents list`)")
    p.add_argument("-m", "--model", help="override the Ollama model for this run")
    p.add_argument("-C", "--cwd", default=".", help="project folder (default: current folder)")
    p.add_argument("-v", "--version", action="version", version=f"bcagent {__version__}")
    a = p.parse_args(argv)

    root = Path(a.cwd).expanduser()
    if not root.is_dir():
        console.print(f"[err]✗ not a folder: {root}[/]")
        sys.exit(1)
    project = Project(root)
    agents = all_agents(project.root)
    name = a.agent or cfg.get("agent", "bulldog")
    agent = agents.get(name)
    if agent is None:
        console.print(f"[err]✗ unknown agent '{name}'[/] – see [bc]bcagent agents list[/]")
        sys.exit(1)
    if a.model:
        agent = Agent(agent.name, a.model, agent.description, agent.instructions,
                      agent.edit_format, agent.temperature, "cli")

    prompt = " ".join(a.prompt).strip() or None
    if a.print_mode and not prompt and not sys.stdin.isatty():
        prompt = sys.stdin.read().strip() or None
    if a.print_mode and not prompt:
        console.print("[err]✗ -p needs a prompt[/]")
        sys.exit(2)

    if not ensure_model(client, agent.model):
        sys.exit(1)
    session = Session(project, agent, cfg, client, auto=a.yes, print_mode=a.print_mode)
    if a.print_mode:
        try:
            session.turn(prompt)
        except OllamaError as e:
            ui.err_console.print(f"[err]✗ {e}[/]")
            sys.exit(1)
        return
    try:
        repl(session, client, cfg, prompt)
    except KeyboardInterrupt:
        console.print()


if __name__ == "__main__":
    main()
