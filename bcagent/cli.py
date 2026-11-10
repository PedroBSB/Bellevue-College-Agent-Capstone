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

from . import __version__, agents as agents_mod, mcp as mcp_mod, ui, update as update_mod
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
    ("/mcp", "MCP servers: /mcp [list|catalog|add <name|@npm/pkg|pypi:pkg>|remove <name>|tools]"),
    ("/run", "Run a Python file: /run [file.py] [args] (default: the last file bcagent wrote)"),
    ("/undo", "Undo the last set of file edits"),
    ("/auto", "Toggle auto-accept of edits (same as shift+tab)"),
    ("/clear", "Clear conversation history"),
    ("/status", "Show agent, model and Ollama status"),
    ("/doctor", "Check your installation"),
    ("/update", "Check for a new bcagent version and install it"),
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
  [bc]/run[/] file.py  run a Python file (bcagent also offers to run the scripts it writes)
  [bc]↑/↓ enter[/]    pick an option when bcagent asks; or choose [bc]Chat about this[/] to reply
  [bc]shift+tab[/]    toggle auto-accept edits
  [bc]esc enter[/]    new line
  [bc]quit[/] / [bc]exit[/]  leave bcagent (also [bc]ctrl+c[/] or [bc]ctrl+d[/] at the prompt)
  [bc]ctrl+c[/]       while bcagent is working: stop the current answer
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
    latest = update_mod.latest_version(force=True)
    if latest and update_mod.newer(latest):
        t.add_row("updates", f"[warn]⬆ v{latest} available[/] – run [bc]bcagent update[/]")
    elif latest:
        t.add_row("updates", f"{ok} up to date")
    else:
        t.add_row("updates", "[muted]could not check (offline?)[/]")
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


# -- MCP servers ---------------------------------------------------------------------

def print_mcp(root: Path, mgr: Optional["mcp_mod.MCPManager"] = None, catalog: bool = False) -> None:
    t = Table(border_style=ui.BC_BLUE_LIGHT, header_style="bc")
    t.add_column("")
    t.add_column("server")
    if catalog:
        t.add_column("command")
        t.add_column("description")
        have = mcp_mod.configured(root)
        for name, e in mcp_mod.CATALOG.items():
            t.add_row("✓" if name in have else "", name, " ".join([e["command"]] + e["args"]), e["description"])
        console.print(t)
        console.print("[muted]Install with[/] [bc]/mcp add <name>[/] [muted]· any npm package ([/][bc]@scope/pkg[/]"
                      "[muted]), PyPI package ([/][bc]pypi:pkg[/][muted]) or[/] "
                      "[bc]bcagent mcp add <name> -- <command> [args][/] [muted]also works.[/]")
        return
    servers = mcp_mod.configured(root)
    if not servers:
        console.print("[muted]No MCP servers yet. See[/] [bc]/mcp catalog[/] [muted]then[/] [bc]/mcp add fetch[/]")
        return
    t.add_column("scope")
    t.add_column("command")
    t.add_column("status")
    for name, sv in servers.items():
        status = ""
        if mgr and name in mgr.clients:
            status = f"[ok]connected[/] · {len(mgr.clients[name].tools)} tools"
        elif mgr and name in mgr.errors:
            status = "[err]failed[/] " + mgr.errors[name][:60]
        elif mgr:
            status = "[muted]connects on first prompt[/]"
        t.add_row("[bc]●[/]", name, sv.get("scope", ""), " ".join([sv.get("command", "")] + sv.get("args", [])),
                  status)
    console.print(t)


def mcp_add(root: Path, spec: str, args: List[str], env: List[str], project: bool,
            mgr: Optional["mcp_mod.MCPManager"] = None, name: Optional[str] = None) -> bool:
    try:
        auto_name, server = mcp_mod.spec_to_server(spec, args)
        if env:
            server["env"] = dict(e.split("=", 1) for e in env if "=" in e)
        name = name or auto_name
        path = mcp_mod.add(name, server, root, project)
    except mcp_mod.MCPError as e:
        console.print(f"[err]✗ {e}[/]")
        return False
    console.print(f"[ok]✓[/] added MCP server [bc]{name}[/] → {ui.shorten(path)}")
    with console.status(f"[bc]Starting {name} (first run may download it)…[/]", spinner="dots",
                        spinner_style=ui.BC_BLUE_LIGHT):
        test = mgr or mcp_mod.MCPManager(root)
        client = test.start_one(name, mcp_mod.configured(root)[name])
    if client:
        console.print(f"  [ok]⎿[/]  connected · {len(client.tools)} tools: "
                      + ", ".join(t["name"] for t in client.tools[:12]))
        if not mgr:
            client.close()
        return True
    console.print(f"  [warn]⎿  saved, but it did not start:[/] {test.errors.get(name)}")
    return False


def print_mcp_tools(mgr: "mcp_mod.MCPManager", server: str = "") -> None:
    if not mgr.started:
        mgr.start()
    for name, err in mgr.errors.items():
        console.print(f"[warn]⚠ {name}:[/] {err}")
    t = Table(border_style=ui.BC_BLUE_LIGHT, header_style="bc")
    t.add_column("tool")
    t.add_column("description")
    for srv, tool in mgr.tools():
        if not server or srv == server:
            t.add_row(f"{srv}.{tool['name']}", " ".join((tool.get("description") or "").split())[:90])
    console.print(t)


def mcp_command(argv: List[str]) -> int:
    p = argparse.ArgumentParser(prog="bcagent mcp", description="Manage MCP servers (Model Context Protocol)")
    sub = p.add_subparsers(dest="cmd")
    sub.add_parser("list", help="list configured MCP servers")
    sub.add_parser("catalog", help="list public MCP servers you can add")
    pa = sub.add_parser("add", help="add a server: catalog name, @npm/package, pypi:package, "
                                    "or <name> -- <command> [args...]")
    pa.add_argument("spec")
    pa.add_argument("args", nargs=argparse.REMAINDER)
    pa.add_argument("-e", "--env", action="append", default=[], help="KEY=VALUE environment variable")
    pa.add_argument("--name", help="name to save the server under")
    pa.add_argument("--project", action="store_true", help=f"save in ./{mcp_mod.PROJECT_FILE} instead of "
                                                           "your user config")
    pr = sub.add_parser("remove", help="remove a server")
    pr.add_argument("name")
    pt = sub.add_parser("tools", help="start the servers and list their tools")
    pt.add_argument("server", nargs="?", default="")
    a = p.parse_args(argv)
    root = Path.cwd()
    if a.cmd in (None, "list"):
        print_mcp(root)
    elif a.cmd == "catalog":
        print_mcp(root, catalog=True)
    elif a.cmd == "add":
        args = a.args[1:] if a.args[:1] == ["--"] else a.args
        return 0 if mcp_add(root, a.spec, args, a.env, a.project, name=a.name) else 1
    elif a.cmd == "remove":
        try:
            path = mcp_mod.remove(a.name, root)
        except mcp_mod.MCPError as e:
            console.print(f"[err]✗ {e}[/]")
            return 1
        console.print(f"[ok]✓[/] removed {a.name} ({ui.shorten(path)})")
    elif a.cmd == "tools":
        mgr = mcp_mod.MCPManager(root)
        try:
            print_mcp_tools(mgr, a.server)
        finally:
            mgr.close()
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
                for f in session.project.complete(word[1:]):
                    yield Completion("@" + f, start_position=-len(word), display=f,
                                     display_meta="folder" if f.endswith("/") else "file")

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
    elif cmd == "/mcp":
        sub, _, rest = arg.partition(" ")
        words = rest.split()
        if sub in ("", "list"):
            print_mcp(proj.root, session.mcp)
        elif sub == "catalog":
            print_mcp(proj.root, catalog=True)
        elif sub == "add" and words:
            project = "--project" in words
            words = [w for w in words if w not in ("--project", "--")]
            env = [w for w in words[1:] if "=" in w and w.split("=")[0].isupper()]
            words = [w for w in words if w not in env]
            mcp_add(proj.root, words[0], words[1:], env, project,
                    session.mcp if session.mcp.started else None)
        elif sub == "remove" and words:
            try:
                mcp_mod.remove(words[0], proj.root)
                c = session.mcp.clients.pop(words[0], None)
                if c:
                    c.close()
                console.print(f"[ok]✓[/] removed {words[0]}")
            except mcp_mod.MCPError as e:
                console.print(f"[err]✗ {e}[/]")
        elif sub == "tools":
            print_mcp_tools(session.mcp, rest.strip())
        else:
            console.print("usage: [bc]/mcp[/] [list | catalog | add <name|@npm/pkg|pypi:pkg> [KEY=VALUE] "
                          "[--project] | remove <name> | tools [server]]")
    elif cmd == "/update":
        if update_mod.update_command() == update_mod.UPDATED:
            console.print("[muted]Restarting bcagent…[/]")
            session.mcp.close()
            update_mod.restart(["-C", str(proj.root), "-a", session.agent.name])
    elif cmd == "/run":
        session.run_file(arg)
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


EXIT_WORDS = {"quit", "exit", "q", ":q"}


def repl(session: Session, client: Ollama, cfg: dict, first: Optional[str]) -> None:
    ui.welcome(session.project.root, session.agent.name, session.agent.model)
    read = make_prompt(session) if ui.is_interactive() else (lambda: input("> "))
    pending = first
    while True:
        try:
            line = pending if pending is not None else read()
            pending = None
        except (KeyboardInterrupt, EOFError):
            break  # ctrl+c / ctrl+d at the prompt leaves bcagent
        line = (line or "").strip()
        if not line:
            continue
        if line.lower() in EXIT_WORDS:
            break
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
    console.print("\n[bc]Go Bulldogs! 🐾[/]")


# -- main ------------------------------------------------------------------------

def main(argv: Optional[List[str]] = None) -> None:
    argv = list(sys.argv[1:] if argv is None else argv)
    if os.name == "nt":
        os.system("")  # enable ANSI colors on old Windows consoles
    cfg = load_config()
    client = Ollama(cfg.get("ollama_host"))

    if argv and argv[0] == "agents":
        sys.exit(agents_command(argv[1:], client, cfg))
    if argv and argv[0] == "mcp":
        sys.exit(mcp_command(argv[1:]))
    if argv and argv[0] == "doctor":
        doctor(client, cfg)
        return
    if argv and argv[0] == "update":
        code = update_mod.update_command()
        sys.exit(0 if code == update_mod.UPDATED else code)

    p = argparse.ArgumentParser(
        prog="bcagent",
        description="bcagent – Bellevue College Agent: a local AI coding CLI (like Claude Code) "
                    "powered by the smallest, fastest local code LLM.",
        epilog="Other commands: `bcagent agents [list|catalog|install|remove|use|create]`, "
               "`bcagent mcp [list|catalog|add|remove|tools]`, `bcagent doctor`, `bcagent update`")
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

    update_mod.maybe_update(cfg, argv, interactive=not a.print_mode and ui.is_interactive())
    if not ensure_model(client, agent.model):
        sys.exit(1)
    session = Session(project, agent, cfg, client, auto=a.yes, print_mode=a.print_mode)
    if a.print_mode:
        try:
            session.turn(prompt)
        except OllamaError as e:
            ui.err_console.print(f"[err]✗ {e}[/]")
            sys.exit(1)
        finally:
            session.mcp.close()
        return
    try:
        repl(session, client, cfg, prompt)
    except KeyboardInterrupt:
        console.print("\n[bc]Go Bulldogs! 🐾[/]")
    finally:
        session.mcp.close()
    sys.exit(0)


if __name__ == "__main__":
    main()
