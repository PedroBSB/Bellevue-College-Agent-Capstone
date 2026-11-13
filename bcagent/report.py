"""Bug reports: when something goes wrong inside bcagent, offer to report it as a GitHub issue.

The report is shown in full before anything is sent and is scrubbed first: your home folder
becomes ~, tokens and keys are removed, and your prompt is only included if you say so. With a
GitHub sign-in (`/github login`, the GitHub CLI or GH_TOKEN) bcagent files the issue from your
account. Without one it opens a pre-filled "New issue" page in your browser, where you submit it.

Turn the question off with `"bug_reports": false` in ~/.bcagent/config.json.
"""
from __future__ import annotations

import platform
import re
import sys
import traceback
import urllib.parse
import webbrowser
from pathlib import Path
from typing import Optional

from rich.markup import escape
from rich.panel import Panel

from . import __version__, ui
from .ui import console

REPO = "PedroBSB/Bellevue-College-Agent-Capstone"
ISSUES_URL = f"https://github.com/{REPO}/issues"
SECRET_PATTERNS = [
    r"gh[pousr]_[A-Za-z0-9]{20,}", r"github_pat_[A-Za-z0-9_]{20,}", r"ya29\.[\w.-]{20,}",
    r"1//[\w-]{20,}", r"GOCSPX-[\w-]{10,}", r"sk-[A-Za-z0-9_-]{20,}", r"AKIA[0-9A-Z]{16}",
    r"(?i)(password|passwd|secret|token|api[_-]?key)(\s*[=:]\s*)\S+",
    r"-----BEGIN [A-Z ]*PRIVATE KEY-----[\s\S]*?-----END [A-Z ]*PRIVATE KEY-----",
]
_asked: set = set()   # error signatures already offered this session


def scrub(text: str) -> str:
    """Remove personal paths and anything that looks like a secret."""
    home = str(Path.home())
    if home and home not in ("/", "\\"):
        text = text.replace(home, "~").replace(home.replace("\\", "/"), "~")
    for pat in SECRET_PATTERNS:
        text = re.sub(pat, lambda m: (m.group(1) + m.group(2) + "[REDACTED]") if m.lastindex and m.lastindex >= 2
                      else "[REDACTED]", text)
    text = re.sub(r"[\w.+-]+@[\w-]+\.[\w.]+", "[email]", text)
    return text


def environment(session=None) -> str:
    lines = [f"- bcagent: v{__version__}",
             f"- Python: {platform.python_version()} ({platform.python_implementation()})",
             f"- OS: {platform.system()} {platform.release()} ({platform.machine()})"]
    if session is not None:
        lines.append(f"- Agent: {session.agent.name} · model {session.agent.model} · edit format "
                     f"{session.edit_format}" + (" · learning mode" if session.learn else ""))
    return "\n".join(lines)


def build(error: Optional[BaseException], context: str = "", description: str = "",
          prompt: str = "", session=None) -> tuple:
    """(title, body) of the issue."""
    if error is not None:
        kind = type(error).__name__
        msg = " ".join(str(error).split())[:90]
        title = f"Bug: {kind}: {msg}" if msg else f"Bug: {kind}"
        tb = "".join(traceback.format_exception(type(error), error, error.__traceback__))[-6000:]
    else:
        title = "Bug: " + (description.splitlines()[0][:90] if description else "problem in bcagent")
        tb = ""
    parts = ["## What happened", description.strip() or "_(no description)_", ""]
    if context:
        parts += [f"**While:** {context}", ""]
    if prompt:
        parts += ["**Request that triggered it:**", "```", prompt.strip()[:1500], "```", ""]
    if tb:
        parts += ["## Error", "```", tb.rstrip(), "```", ""]
    parts += ["## Environment", environment(session), "",
              "_Reported from bcagent (`/bug`). Personal paths and secrets were removed._"]
    return scrub(title), scrub("\n".join(parts))


def _similar(title: str) -> Optional[dict]:
    """An open issue with the same error, to avoid duplicates."""
    from .git import GitError, api
    key = re.sub(r"[^\w\s:]", " ", title.replace("Bug: ", ""))[:60].strip()
    q = urllib.parse.quote(f'repo:{REPO} is:issue is:open in:title "{key}"')
    try:
        data, _ = api("GET", f"/search/issues?q={q}&per_page=1")
    except GitError:
        return None
    items = data.get("items") or []
    return items[0] if items else None


def submit(title: str, body: str) -> Optional[str]:
    """File the issue. Returns its URL, or None if it was opened in the browser / not sent."""
    from . import git as git_mod
    if not git_mod.token():
        c = ui.choose("You're not signed in to GitHub. How do you want to send the report?",
                      ["Open a pre-filled issue in my browser (I'll click Submit)",
                       "Sign in to GitHub first, then bcagent sends it", "Don't send"], cancel=2)
        if c == 2:
            return None
        if c == 1 and not git_mod.login():
            return None
        if c == 0:
            url = f"{ISSUES_URL}/new?" + urllib.parse.urlencode({"title": title, "body": body[:6000],
                                                                    "labels": "bug"})
            webbrowser.open(url)
            console.print(f"[muted]Opened GitHub in your browser – review it and click[/] [bold]Submit new "
                          f"issue[/][muted]. If nothing opened, go to[/] {ISSUES_URL}/new")
            return None
    dup = _similar(title)
    if dup:
        console.print(f"[muted]There is already an open report for this:[/] [bc]#{dup['number']}[/] "
                      f"{escape(dup['title'])}\n  {dup['html_url']}")
        c = ui.choose("What do you want to do?", ["Add my details as a comment there", "Open a new issue anyway",
                                                  "Don't send"], cancel=2)
        if c == 2:
            return None
        if c == 0:
            data, _ = git_mod.api("POST", f"/repos/{REPO}/issues/{dup['number']}/comments", {"body": body})
            return data.get("html_url") or dup["html_url"]
    data, _ = git_mod.api("POST", f"/repos/{REPO}/issues", {"title": title, "body": body, "labels": ["bug"]})
    return data.get("html_url")


def offer(error: Optional[BaseException] = None, context: str = "", prompt: str = "", session=None,
          description: str = "", ask: bool = True) -> None:
    """Ask the user whether to report a problem, show the report, and send it if they agree."""
    from . import git as git_mod
    from .config import load_config
    if not ui.is_interactive() or (session is not None and session.print_mode):
        if error is not None:
            ui.err_console.print(f"[muted]To report this bug run bcagent and type /bug, or open {ISSUES_URL}[/]")
        return
    if error is not None:
        sig = f"{type(error).__name__}:{str(error)[:80]}"
        if sig in _asked:
            return
        _asked.add(sig)
    if ask:
        if not load_config().get("bug_reports", True):
            return
        c = ui.choose("This looks like a bug in bcagent. Report it to the bcagent developers on GitHub?",
                      ["Yes, show me the report first", "No", "No, and don't ask again"], cancel=1)
        if c == 2:
            from .config import save_config
            cfg = load_config()
            cfg["bug_reports"] = False
            save_config(cfg)
            console.print("[muted]OK – you can still report problems with[/] [bc]/bug[/]")
            return
        if c != 0:
            return
    if not description:
        try:
            description = console.input("  [muted]What were you doing / what did you expect? "
                                        "(optional, enter to skip)[/]\n  [bc]❯[/] ").strip()
        except (EOFError, KeyboardInterrupt):
            return
    include_prompt = False
    if prompt:
        include_prompt = ui.choose("Include the request you typed? (it may contain your code or private "
                                   "information)", ["No", "Yes"], cancel=0) == 1
    title, body = build(error, context, description, prompt if include_prompt else "", session)
    while True:
        console.print(Panel(escape(f"{title}\n\n{body}"), title="bug report preview", border_style=ui.BC_BLUE_LIGHT))
        c = ui.choose(f"Send this report to {REPO} from your GitHub account?",
                      ["Send it", "Edit the description", "Don't send"], cancel=2)
        if c == 2:
            console.print("[muted]Not sent.[/]")
            return
        if c == 1:
            description = console.input("  [muted]description[/] [bc]❯[/] ").strip()
            title, body = build(error, context, description, prompt if include_prompt else "", session)
            continue
        break
    try:
        url = submit(title, body)
    except git_mod.GitError as e:
        console.print(f"[err]✗ could not send the report: {e}[/]\n  [muted]You can open an issue by hand at[/] "
                      f"{ISSUES_URL}/new")
        return
    if url:
        console.print(f"[ok]✓[/] reported – thank you! [bc]{url}[/]")


def crash_hook(exc_type, exc, tb) -> None:
    """sys.excepthook for crashes outside the prompt loop: print a short message and offer a report."""
    if issubclass(exc_type, (KeyboardInterrupt, SystemExit)):
        return sys.__excepthook__(exc_type, exc, tb)
    console.print(f"[err]✗ bcagent crashed: {escape(exc_type.__name__)}: {escape(str(exc))}[/]")
    try:
        offer(exc, context="starting bcagent")
    except Exception:  # never fail while reporting a failure
        traceback.print_exception(exc_type, exc, tb)
