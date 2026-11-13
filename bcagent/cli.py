"""Command line entry point: `bcagent`."""
from __future__ import annotations

import argparse
import os
import platform
import sys
from pathlib import Path
from typing import List, Optional

from rich.markup import escape
from rich.progress import BarColumn, DownloadColumn, Progress, TextColumn, TransferSpeedColumn
from rich.table import Table

from . import __version__, actions as actions_mod, agents as agents_mod, course as course_mod, hardware as hardware_mod, colab as colab_mod, git as git_mod, packages as packages_mod, report as report_mod, mcp as mcp_mod, ui, update as update_mod
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
    ("/git", "Git: /git [status|diff|commit|push|pull|log|branch|switch|merge|revert|uncommit|init|clone|...]"),
    ("/github", "GitHub: /github [login|logout|status|create|pr|prs|open|clone]"),
    ("/colab", "Edit a Google Colab notebook: /colab <url> [pull|push|open|list|unlink|login|logout]"),
    ("/test", "Run the tests (pytest); bcagent offers to fix failures and runs them again"),
    ("/review", "Get instructor-style feedback on your code: /review [@file ...] (default: changed files)"),
    ("/learn", "Learning mode on/off: hints and explanations instead of finished code"),
    ("/export", "Save this session as a Markdown log (to hand in with an assignment)"),
    ("/resume", "Continue an earlier conversation in this folder"),
    ("/bug", "Report a problem with bcagent on GitHub: /bug [what happened]"),
    ("/packages", "Check / install the default libraries (numpy, pandas, scikit-learn, statsmodels, statsformula)"),
    ("/course", "Show the course profile your instructor set (learning mode, exam times, allowed agents)"),
    ("/run", "Run a Python file: /run [file.py] [args] (default: the last file bcagent wrote)"),
    ("/undo", "Undo the last set of file edits"),
    ("/auto", "Toggle auto-accept of edits (same as shift+tab)"),
    ("/clear", "Clear conversation history"),
    ("/status", "Show agent, model and Ollama status"),
    ("/doctor", "Check your installation"),
    ("/update", "Update bcagent: /update [--to <version> | --list]"),
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
  [bc]/git[/]          git status · [bc]/git commit[/] · [bc]/git push[/] · [bc]/git revert[/] … ([bc]/git help[/])
  [bc]/github create[/] put this folder on GitHub · [bc]/github pr[/] open a pull request
  [bc]/test[/]         run the tests · [bc]/review[/] feedback on your code · [bc]/learn[/] hints instead of answers
  [bc]/export[/]       save a log of this session · [bc]/resume[/] continue an earlier one · [bc]/bug[/] report a problem
  [bc]/colab[/] url    edit a Google Colab notebook (bcagent helps you sign in to Google)
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
    import shutil as _sh
    gitexe = _sh.which("git")
    t.add_row("git", f"{ok} {gitexe}" if gitexe else f"{bad} not found – https://git-scm.com/downloads")
    t.add_row("github", f"{ok} {git_mod.token_source()[1]}" if git_mod.token() else
              "[muted]not signed in – /github login[/]")
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
    t.add_row("libraries", packages_mod.status(cfg))
    ram = hardware_mod.total_ram_gb()
    best, size = hardware_mod.recommend(ram)
    current = cfg.get("agent", "bulldog")
    t.add_row("suggested agent", f"{best} ({size})" + (f" for {ram:.0f} GB RAM" if ram else "") +
              ("  [ok]✓ in use[/]" if best == current else f"  [muted]– bcagent agents install {best}[/]"))
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
    sub.add_parser("recommend", help="the best agent for this computer's memory")
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
    elif a.cmd == "recommend":
        ram = hardware_mod.total_ram_gb()
        best, size = hardware_mod.recommend(ram)
        console.print(f"This computer has {ram:.0f} GB of RAM → [bc]{best}[/] ({size} download)." if ram else
                      f"Could not read the RAM size → [bc]{best}[/].")
        console.print(f"[muted]Install and use it with[/] [bc]bcagent agents install {best}[/] [muted]then[/] "
                      f"[bc]bcagent agents use {best}[/]")
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


# -- Google Colab --------------------------------------------------------------------

COLAB_HELP = """[bold]/colab[/] – let bcagent edit a Google Colab notebook

  [bc]/colab <url>[/]       link a notebook (https://colab.research.google.com/drive/…)
  [bc]/colab pull[/]        download the latest version from Colab
  [bc]/colab push[/]        upload the local copy to Colab ([bc]--force[/] ignores changes made in Colab)
  [bc]/colab open[/]        open the notebook in your browser
  [bc]/colab list[/]        linked notebooks and sign-in status
  [bc]/colab unlink[/]      stop syncing a notebook (the local file stays)
  [bc]/colab login[/]       sign in to Google (bcagent guides you)  ·  [bc]/colab logout[/]

[muted]bcagent copies the notebook to colab/<name>.py (cells start with '# %%'). Ask for changes as usual, \
for example "add a cell that plots the data". After you approve an edit, bcagent saves it to Colab. \
Reload the Colab tab to see it.[/]"""


def print_colab(sync: "colab_mod.ColabSync") -> None:
    creds = sync.drive.creds
    who = creds.token().get("email", "")
    console.print(f"Google sign-in: [bc]{creds.kind()}[/]" + (f" [muted]({who})[/]" if who else ""))
    if not sync.links:
        console.print("[muted]No notebooks linked yet – use[/] [bc]/colab <url>[/]")
        return
    t = Table(border_style=ui.BC_BLUE_LIGHT, header_style="bc")
    t.add_column("local file")
    t.add_column("notebook")
    t.add_column("link")
    for rel, link in sync.links.items():
        t.add_row(rel, link.get("name", "") + ("" if link.get("can_edit", True) else " [warn](view only)[/]"),
                  link.get("url", ""))
    console.print(t)


def _pick_mirror(sync: "colab_mod.ColabSync", arg: str, session: Optional[Session] = None) -> Optional[str]:
    arg = arg.lstrip("@").strip()
    if arg:
        if arg in sync.links:
            return arg
        try:
            fid = colab_mod.parse_url(arg)
            rel = sync.mirror_for(fid)
            if rel:
                return rel
        except colab_mod.ColabError:
            pass
        console.print(f"[err]✗ {escape(arg)} is not a linked notebook[/] – see [bc]/colab list[/]")
        return None
    if not sync.links:
        console.print("[muted]No notebooks linked yet – use[/] [bc]/colab <url>[/]")
        return None
    if len(sync.links) == 1:
        return next(iter(sync.links))
    pinned = [p for p in (session.project.pinned if session else []) if p in sync.links]
    if len(pinned) == 1:
        return pinned[0]
    names = list(sync.links)
    if not ui.is_interactive():
        console.print("[err]✗ more than one notebook is linked – name the file[/]")
        return None
    c = ui.choose("Which notebook?", names + ["Cancel"], cancel=len(names))
    return names[c] if c < len(names) else None


def colab_link(sync: "colab_mod.ColabSync", url: str, session: Optional[Session] = None) -> Optional[str]:
    try:
        colab_mod.parse_url(url)  # check the link before asking to sign in
        if not sync.ensure_signed_in():
            return None
        with console.status("[bc]Downloading the notebook from Google Drive…[/]", spinner="dots",
                            spinner_style=ui.BC_BLUE_LIGHT):
            rel = sync.link(url)
    except colab_mod.ColabError as e:
        console.print(f"[err]✗ {e}[/]")
        return None
    link = sync.links[rel]
    cells = len(colab_mod.text_to_cells((sync.root / rel).read_text(encoding="utf-8")))
    console.print(f"[ok]✓[/] linked [bc]{escape(link['name'])}[/] ({cells} cells) → [bc]{rel}[/]")
    if not link.get("can_edit", True):
        console.print("  [warn]⚠ you can only view this notebook.[/] In Colab choose File ▸ Save a copy in Drive "
                      "and link the copy to let bcagent save changes.")
    if session is not None:
        if rel not in session.project.pinned:
            session.project.pinned.append(rel)
        console.print(f"  [muted]⎿  Ask for changes, e.g.[/] [bc]add a cell that plots the data[/] [muted]– bcagent "
                      "saves approved edits to Colab. Reload the Colab tab to see them, and avoid editing the "
                      "same notebook in Colab at the same time.[/]")
    return rel


def handle_colab(arg: str, session: Session) -> None:
    sync = session.colab
    sub, _, rest = arg.partition(" ")
    rest = rest.strip()
    try:
        if sub in ("help", "-h", "--help"):
            console.print(COLAB_HELP)
        elif sub in ("", "list", "status"):
            if not sub and not sync.links:
                console.print(COLAB_HELP)
            print_colab(sync)
        elif sub == "login":
            colab_mod.login()
        elif sub == "logout":
            console.print("[ok]✓[/] signed out of Google" if colab_mod.logout() else "[muted]Not signed in.[/]")
        elif sub in ("pull", "push", "open", "unlink"):
            force = "--force" in rest.split()
            rel = _pick_mirror(sync, " ".join(w for w in rest.split() if w != "--force"), session)
            if not rel:
                return
            if sub == "open":
                import webbrowser
                webbrowser.open(sync.links[rel]["url"])
                console.print(f"[muted]Opened[/] {sync.links[rel]['url']}")
            elif sub == "unlink":
                sync.unlink(rel)
                session.project.pinned = [p for p in session.project.pinned if p != rel]
                console.print(f"[ok]✓[/] {rel} is no longer synced with Colab (the file stays)")
            elif not sync.ensure_signed_in():
                return
            elif sub == "pull":
                with console.status("[bc]Downloading from Colab…[/]", spinner="dots", spinner_style=ui.BC_BLUE_LIGHT):
                    n = sync.pull(rel)
                console.print(f"[ok]✓[/] {rel} updated from Colab ({n} cells)")
            elif sync.push(rel, force=force):
                console.print(f"[ok]✓[/] saved {rel} to Colab [muted](reload the Colab tab to see it)[/]")
        else:
            colab_link(sync, arg, session)
    except colab_mod.ColabError as e:
        console.print(f"[err]✗ {e}[/]")


def colab_command(argv: List[str]) -> int:
    p = argparse.ArgumentParser(prog="bcagent colab", description="Google Colab notebooks")
    sub = p.add_subparsers(dest="cmd")
    sub.add_parser("login", help="sign in to Google (guided)")
    sub.add_parser("logout", help="sign out of Google")
    sub.add_parser("list", help="sign-in status and linked notebooks in this folder")
    pl = sub.add_parser("link", help="link a notebook to this folder")
    pl.add_argument("url")
    for name in ("pull", "push"):
        sp = sub.add_parser(name, help=f"{name} a linked notebook")
        sp.add_argument("file", nargs="?", default="")
        if name == "push":
            sp.add_argument("--force", action="store_true")
    a = p.parse_args(argv)
    sync = colab_mod.ColabSync(Path.cwd())
    try:
        if a.cmd == "login":
            return 0 if colab_mod.login() else 1
        if a.cmd == "logout":
            colab_mod.logout()
            console.print("[ok]✓[/] signed out of Google")
        elif a.cmd in (None, "list"):
            print_colab(sync)
        elif a.cmd == "link":
            return 0 if colab_link(sync, a.url) else 1
        else:
            rel = _pick_mirror(sync, a.file)
            if not rel or not sync.ensure_signed_in():
                return 1
            if a.cmd == "pull":
                console.print(f"[ok]✓[/] {rel} updated from Colab ({sync.pull(rel)} cells)")
            elif sync.push(rel, force=a.force):
                console.print(f"[ok]✓[/] saved {rel} to Colab")
            else:
                return 1
    except colab_mod.ColabError as e:
        console.print(f"[err]✗ {e}[/]")
        return 1
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
        session.events.clear()
        session.session_id = __import__("datetime").datetime.now().strftime("%Y%m%d-%H%M%S")
        console.clear()
        console.print("[muted]Conversation cleared.[/]")
    elif cmd == "/agents":
        print_agents(proj.root, session.agent.name, catalog=(arg == "catalog"))
    elif cmd in ("/agent", "/install", "/model") and session.course and session.course.allowed_agents and (
            cmd == "/model" and arg or cmd != "/model" and arg and not session.course.agent_allowed(arg)):
        console.print(f"[warn]✋ Your course profile allows only these agents:[/] "
                      f"{', '.join(session.course.allowed_agents)}" +
                      (" [muted](and no other models)[/]" if cmd == "/model" else ""))
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
        elif sub == "add" and words and session.course and not session.course.allow_mcp:
            console.print("[warn]✋ MCP servers are turned off by your course profile.[/]")
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
        if update_mod.update_command(arg.split()) == update_mod.UPDATED:
            console.print("[muted]Restarting bcagent…[/]")
            session.mcp.close()
            update_mod.restart(["-C", str(proj.root), "-a", session.agent.name])
    elif cmd in ("/git", "/github"):
        try:
            g = git_mod.GitCommands(proj.root, session)
            (g.git if cmd == "/git" else g.github)(arg)
        except git_mod.GitError as e:
            console.print(f"[err]✗ {e}[/]")
    elif cmd == "/colab":
        handle_colab(arg, session)
    elif cmd == "/test":
        session.run_tests(arg)
    elif cmd == "/review":
        session.review_code(arg)
    elif cmd == "/learn" and session.course and session.course.learn_locked and not (arg == "on"):
        console.print("[warn]✋ Learning mode is locked on by your course profile[/] "
                      f"[muted]({escape(session.course.name or 'course')}). /course shows the rules.[/]")
    elif cmd == "/course":
        print_course(session.course)
    elif cmd == "/learn":
        session.learn = (arg == "on") or (arg != "off" and not session.learn)
        if session.learn:
            console.print("[ok]✓[/] learning mode [bold]on[/]: bcagent explains and gives hints but doesn't write "
                          "the solution or edit files. [muted]/learn again to turn it off.[/]")
        else:
            console.print("learning mode [bold]off[/]: bcagent writes code and edits files again.")
    elif cmd == "/export":
        try:
            path = session.export(arg)
            console.print(f"[ok]✓[/] saved the session log to [bc]{proj.rel(path)}[/]")
        except (OSError, ValueError) as e:
            console.print(f"[err]✗ {e}[/]")
    elif cmd == "/resume":
        resume(session, pick=True)
    elif cmd == "/bug":
        last = getattr(session, "last_error", None)
        err, line_ = last if last else (None, "")
        report_mod.offer(err, context=f"handling `{line_[:80]}`" if line_ else "", prompt=line_,
                         session=session, description=arg, ask=False)
    elif cmd == "/packages":
        left = packages_mod.ensure(cfg)
        console.print(packages_mod.status(cfg) if not left else f"[warn]still missing: {', '.join(left)}[/]")
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


def print_course(course) -> None:
    if not course:
        console.print("[muted]No course profile. Instructors: see[/] [bc]bcagent course create[/]")
        return
    t = Table.grid(padding=(0, 2))
    t.add_column(style="muted")
    t.add_column()
    t.add_row("course", f"[bc]{escape(course.name or '(unnamed)')}[/]" +
              (f"  [muted]· {escape(course.instructor)}[/]" if course.instructor else ""))
    t.add_row("learning mode", {"off": "off", "on": "on (you can turn it off with /learn)",
                                "locked": "[bold]locked on[/]"}[course.learn_mode])
    t.add_row("agents", ", ".join(course.allowed_agents) if course.allowed_agents else "any")
    t.add_row("MCP tools", "allowed" if course.allow_mcp else "turned off")
    t.add_row("session log", "saved automatically in bcagent-logs/" if course.session_log == "auto" else "off")
    if course.exam_start and course.exam_end:
        state = ("[warn]now[/]" if course.exam_active() else
                 "[muted]over[/]" if datetime.now() > course.exam_end else "[muted]upcoming[/]")
        t.add_row("exam", f"{course.exam_start:%b %d %H:%M} – {course.exam_end:%H:%M} ({state}) · " +
                  ("no AI help" if course.exam_mode == "blocked" else "hints about errors and concepts only"))
    t.add_row("profile", f"{ui.shorten(Path(course.source))} · fingerprint {course.fingerprint}")
    console.print(t)
    if course.notes:
        console.print("[muted]notes for the agent:[/] " + escape(course.notes.splitlines()[0][:100]) +
                      (" …" if len(course.notes) > 100 or "\n" in course.notes else ""))


def course_command(argv: List[str]) -> int:
    sub = argv[0] if argv else "show"
    root = Path.cwd()
    try:
        if sub == "show":
            print_course(course_mod.load(root))
        elif sub == "create":
            path = course_mod.create(root)
            console.print(f"[ok]✓[/] course profile: [bc]{path}[/]\n  [muted]Edit it, commit it with the assignment's "
                          "starter files, and every student who runs bcagent in that folder gets these rules. "
                          "Check it with[/] [bc]bcagent course show[/]")
        elif sub == "install" and len(argv) > 1:
            path = course_mod.install(argv[1])
            console.print(f"[ok]✓[/] installed for every project on this computer: [bc]{path}[/]")
            print_course(course_mod.load(root))
        elif sub == "remove":
            existed = course_mod.USER_FILE.exists()
            if existed:
                course_mod.USER_FILE.unlink()
            console.print("[ok]✓[/] removed the computer-wide course profile" if existed else
                          "[muted]No computer-wide course profile (a project's .bcagent/course.md stays).[/]")
        else:
            console.print("usage: bcagent course [show | create | install <file.md|url> | remove]")
            return 2
    except (OSError, ValueError) as e:
        console.print(f"[err]✗ {e}[/]")
        return 1
    return 0


def resume(session: Session, pick: bool) -> bool:
    saved = session.saved_sessions()
    if not saved:
        console.print("[muted]No earlier conversations in this folder.[/]")
        return False
    data = saved[0]
    if pick and len(saved) > 1 and ui.is_interactive():
        from datetime import datetime
        labels = [f"{datetime.fromtimestamp(d.get('saved', 0)).strftime('%b %d %H:%M')} · "
                  f"{len(d.get('history', [])) // 2} exchange(s) · {d.get('title') or '(no title)'}"[:100]
                  for d in saved[:8]]
        c = ui.choose("Which conversation?", labels + ["Cancel"], cancel=len(labels))
        if c >= len(labels):
            return False
        data = saved[c]
    session.restore(data)
    return True


def run_request(line: str, session: Session, client: Ollama, cfg: dict) -> None:
    """A typed request: built-in actions (commit, push, tests, colab…) or a task for the agent."""
    plan = actions_mod.route(line)
    if not plan.commands:
        return session.turn(line)
    interactive = ui.is_interactive() and not session.print_mode
    links = [c for c in plan.commands if c.startswith("/colab http")]
    others = [c for c in plan.commands if c not in links]
    for c in links:  # a pasted Colab link: link it first, then do the task on it
        handle_slash(c, session, client, cfg)
    if plan.action_only and others:
        shown = " → ".join(others)
        if interactive:
            c = ui.choose(f"bcagent can do this itself: [bc]{escape(shown)}[/]",
                          ["Yes, do it", "No, send my message to the agent instead"], cancel=1)
            if c == 1:
                return session.turn(line)
        elif not session.auto:
            return session.turn(line)
        for cmd in others:
            handle_slash(cmd, session, client, cfg)
        return None
    if plan.rest:
        session.turn(plan.rest)
    if others:
        shown = " → ".join(others)
        if interactive:
            if ui.choose(f"You also asked to [bc]{escape(shown)}[/]. Do it now?", ["Yes", "No"], cancel=1) != 0:
                return None
        elif not session.auto:
            return None
        for cmd in others:
            handle_slash(cmd, session, client, cfg)
    return None


def repl(session: Session, client: Ollama, cfg: dict, first: Optional[str]) -> None:
    ui.welcome(session.project.root, session.agent.name, session.agent.model,
               course=session.course.summary() if session.course else "")
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
                run_request(line, session, client, cfg)
        except KeyboardInterrupt:
            console.print("  [warn]⎿  Interrupted[/]")
        except OllamaError as e:
            console.print(f"[err]✗ {e}[/]")
        except Exception as e:  # a bug in bcagent: keep the session alive and offer to report it
            session.last_error = (e, line)
            console.print(f"[err]✗ Something went wrong inside bcagent: {escape(type(e).__name__)}: "
                          f"{escape(str(e)[:200])}[/]")
            try:
                report_mod.offer(e, context=f"handling `{line[:80]}`", prompt=line, session=session)
            except Exception:
                pass
        console.print()
    console.print("\n[bc]Go Bulldogs! 🐾[/]")


# -- main ------------------------------------------------------------------------

def main(argv: Optional[List[str]] = None) -> None:
    argv = list(sys.argv[1:] if argv is None else argv)
    if os.name == "nt":
        os.system("")  # enable ANSI colors on old Windows consoles
    cfg = load_config()
    client = Ollama(cfg.get("ollama_host"))
    if ui.is_interactive():
        sys.excepthook = report_mod.crash_hook

    if argv and argv[0] == "agents":
        sys.exit(agents_command(argv[1:], client, cfg))
    if argv and argv[0] == "mcp":
        sys.exit(mcp_command(argv[1:]))
    if argv and argv[0] in ("git", "github"):
        try:
            g = git_mod.GitCommands(Path.cwd())
            (g.git if argv[0] == "git" else g.github)(" ".join(argv[1:]))
        except git_mod.GitError as e:
            console.print(f"[err]✗ {e}[/]")
            sys.exit(1)
        return
    if argv and argv[0] == "colab":
        sys.exit(colab_command(argv[1:]))
    if argv and argv[0] == "doctor":
        doctor(client, cfg)
        return
    if argv and argv[0] == "course":
        sys.exit(course_command(argv[1:]))
    if argv and argv[0] == "packages":
        sys.exit(packages_mod.command(argv[1:]))
    if argv and argv[0] == "bug":
        report_mod.offer(None, description=" ".join(argv[1:]), ask=False)
        return
    if argv and argv[0] == "update":
        code = update_mod.update_command(argv[1:])
        sys.exit(0 if code == update_mod.UPDATED else code)

    p = argparse.ArgumentParser(
        prog="bcagent",
        description="bcagent – Bellevue College Agent: a local AI coding CLI (like Claude Code) "
                    "powered by the smallest, fastest local code LLM.",
        epilog="Other commands: `bcagent agents [list|catalog|install|remove|use|create]`, "
               "`bcagent mcp [list|catalog|add|remove|tools]`, `bcagent colab [login|logout|list|link|pull|push]`, "
               "`bcagent github [login|status|create|pr]`, `bcagent git <command>`, "
               "`bcagent doctor`, `bcagent packages`, `bcagent update [--to X.Y.Z | --list]`, `bcagent bug`")
    p.add_argument("prompt", nargs="*", help="initial prompt")
    p.add_argument("-p", "--print", action="store_true", dest="print_mode",
                   help="non-interactive: answer the prompt, print the result and exit")
    p.add_argument("-y", "--yes", action="store_true", help="auto-accept file edits")
    p.add_argument("-a", "--agent", help="agent to use (see `bcagent agents list`)")
    p.add_argument("-m", "--model", help="override the Ollama model for this run")
    p.add_argument("-C", "--cwd", default=".", help="project folder (default: current folder)")
    p.add_argument("--colab", metavar="URL", help="link a Google Colab notebook and edit it")
    p.add_argument("--learn", action="store_true", help="learning mode: hints and explanations, no finished code")
    p.add_argument("-c", "--continue", action="store_true", dest="cont",
                   help="continue the most recent conversation in this folder")
    p.add_argument("-r", "--resume", action="store_true", help="pick an earlier conversation to continue")
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
    try:
        course = course_mod.load(project.root)
    except (OSError, ValueError):
        course = None
    if agent is None and course and course.allowed_agents and not course.agent_allowed(name):
        agent = agents.get("bulldog")  # replaced below by the course profile's agent
    if agent is None:
        console.print(f"[err]✗ unknown agent '{name}'[/] – see [bc]bcagent agents list[/]")
        sys.exit(1)
    if course and course.allowed_agents:
        if a.model:
            console.print("[warn]✋ -m is turned off by your course profile[/] [muted](it allows the agents "
                          f"{', '.join(course.allowed_agents)})[/]")
            a.model = None
        if not course.agent_allowed(agent.name) or not course.agent_allowed(name):
            want = course.default_agent if course.agent_allowed(course.default_agent or "-") else \
                course.allowed_agents[0]
            if a.agent or name != "bulldog":
                console.print(f"[warn]✋ Your course profile doesn't allow the agent {name}; using {want}.[/]")
            agent = agents.get(want)
            if agent is None and want in CATALOG:
                agent = install_agent(client, want)
            if agent is None:
                console.print(f"[err]✗ the course profile's agent '{want}' is not installed[/] – run "
                              f"[bc]bcagent agents install {want}[/]")
                sys.exit(1)
    elif course and course.default_agent and not a.agent and course.default_agent in agents:
        agent = agents[course.default_agent]
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
    if not a.print_mode:
        try:
            packages_mod.ensure(cfg)   # numpy, pandas, scikit-learn, statsmodels, statsformula
        except Exception as e:  # never block start-up
            console.print(f"[warn]⚠ could not check the default libraries: {escape(str(e))}[/]")
    session = Session(project, agent, cfg, client, auto=a.yes, print_mode=a.print_mode)
    if a.learn:
        session.learn = True
    if a.cont or a.resume:
        resume(session, pick=a.resume)
    if not a.print_mode and not a.agent and not a.model:
        try:
            hardware_mod.maybe_suggest(session, cfg, client)
        except Exception:  # a suggestion must never stop bcagent from starting
            pass
    if a.colab:
        rel = colab_link(session.colab, a.colab, session)
        if not rel:
            sys.exit(1)
        if prompt and rel not in prompt:
            prompt += f" (in @{rel})"
    if a.print_mode:
        try:
            run_request(prompt, session, client, cfg)
        except OllamaError as e:
            ui.err_console.print(f"[err]✗ {e}[/]")
            sys.exit(1)
        except Exception as e:
            ui.err_console.print(f"[err]✗ bcagent error: {type(e).__name__}: {e}[/]\n"
                                 f"[muted]Please report it: run [bc]bcagent bug[/] or open {report_mod.ISSUES_URL}[/]")
            sys.exit(1)
        finally:
            session.mcp.close()
            session.autosave_log()
        return
    try:
        repl(session, client, cfg, prompt)
    except KeyboardInterrupt:
        console.print("\n[bc]Go Bulldogs! 🐾[/]")
    finally:
        session.mcp.close()
        saved = session.autosave_log()
        if saved:
            console.print(f"[muted]Session log saved to {session.project.rel(saved)} (course profile)[/]")
    sys.exit(0)


if __name__ == "__main__":
    main()
