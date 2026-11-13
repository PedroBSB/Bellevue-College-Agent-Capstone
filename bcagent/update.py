"""Self-update: look for a newer bcagent on GitHub and upgrade in place.

bcagent checks the latest GitHub *release* at most every few hours (cached in ~/.bcagent/update.json,
3 second timeout, silent when offline). Only tagged releases (vX.Y.Z), which pass the tests in CI on
macOS, Linux and Windows, reach users; pushes to the main branch don't. When a newer version exists and bcagent was set up by the
installer (it runs from ~/.bcagent/venv), it upgrades itself with pip and restarts into the new version.
Other installs (pip, editable checkouts) only get a notice.

Turn it off with `"auto_update": false` in ~/.bcagent/config.json or BCAGENT_NO_UPDATE=1.
Roll back with `bcagent update --to 1.5.0`; that pins the version (no automatic updates) until a plain
`bcagent update`. BCAGENT_UPDATE_CHANNEL=main follows the main branch instead (for developers).
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import time
import urllib.request
from pathlib import Path
from typing import List, Optional, Tuple

from . import __version__
from .config import HOME
from .ui import console, err_console

REPO = "PedroBSB/Bellevue-College-Agent-Capstone"
CHANNEL = os.environ.get("BCAGENT_UPDATE_CHANNEL", "release")
LATEST_RELEASE_URL = f"https://github.com/{REPO}/releases/latest"
RELEASES_API = f"https://api.github.com/repos/{REPO}/releases?per_page=30"
VERSION_URL = os.environ.get("BCAGENT_UPDATE_URL",
                             f"https://raw.githubusercontent.com/{REPO}/main/bcagent/__init__.py")
MAIN_SOURCE = f"https://github.com/{REPO}/archive/refs/heads/main.zip"
SOURCE = os.environ.get("BCAGENT_UPDATE_SOURCE", MAIN_SOURCE)


def source_for(version: Optional[str]) -> str:
    """pip source for a version: the tagged release zip (or the main branch on the main channel)."""
    if os.environ.get("BCAGENT_UPDATE_SOURCE"):
        return SOURCE
    if CHANNEL == "main" or not version:
        return MAIN_SOURCE
    return f"https://github.com/{REPO}/archive/refs/tags/v{version.lstrip('v')}.zip"
CACHE = HOME / "update.json"
CHECK_EVERY = 6 * 3600  # seconds between checks


def parse_version(v: str) -> Tuple[int, ...]:
    return tuple(int(x) for x in re.findall(r"\d+", v or "")[:3])


def newer(latest: Optional[str], current: str = __version__) -> bool:
    return bool(latest) and parse_version(latest) > parse_version(current)


def fetch_latest(timeout: float = 3.0) -> Optional[str]:
    """The newest released version on GitHub, or None when offline."""
    if CHANNEL == "main" or os.environ.get("BCAGENT_UPDATE_URL"):
        return fetch_main(timeout)
    # github.com/<repo>/releases/latest redirects to .../releases/tag/vX.Y.Z (no API rate limit)
    req = urllib.request.Request(LATEST_RELEASE_URL, method="HEAD",
                                 headers={"User-Agent": f"bcagent/{__version__}"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            final = r.geturl()
    except Exception:
        return None
    m = re.search(r"/releases/tag/v?([0-9][^/?#]*)$", final)
    return m.group(1) if m else None


def list_releases(timeout: float = 10.0) -> List[str]:
    """Released versions, newest first (GitHub API)."""
    req = urllib.request.Request(RELEASES_API, headers={"User-Agent": f"bcagent/{__version__}",
                                                       "Accept": "application/vnd.github+json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            data = json.load(r)
    except Exception:
        return []
    return [d["tag_name"].lstrip("v") for d in data if not d.get("draft") and not d.get("prerelease")]


def fetch_main(timeout: float = 3.0) -> Optional[str]:
    """The version on the main branch."""
    req = urllib.request.Request(VERSION_URL, headers={"Cache-Control": "no-cache",
                                                       "User-Agent": f"bcagent/{__version__}"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            text = r.read(20000).decode("utf-8", "replace")
    except Exception:
        return None
    m = re.search(r"""__version__\s*=\s*["']([^"']+)["']""", text)
    return m.group(1) if m else None


def latest_version(force: bool = False) -> Optional[str]:
    """Cached lookup of the latest version (network at most every CHECK_EVERY seconds)."""
    cache = {}
    try:
        cache = json.loads(CACHE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        pass
    if not force and time.time() - cache.get("checked", 0) < CHECK_EVERY:
        return cache.get("latest")
    latest = fetch_latest() or cache.get("latest")
    try:
        CACHE.parent.mkdir(parents=True, exist_ok=True)
        CACHE.write_text(json.dumps({"checked": time.time(), "latest": latest}), encoding="utf-8")
    except OSError:
        pass
    return latest


def is_managed() -> bool:
    """True when bcagent runs from the installer's virtual environment, which it may upgrade itself."""
    try:
        return Path(sys.prefix).resolve() == (HOME / "venv").resolve()
    except OSError:
        return False


def install(source: Optional[str] = None) -> bool:
    source = source or SOURCE
    cmd = [sys.executable, "-m", "pip", "install", "--quiet", "--disable-pip-version-check",
           "--upgrade", "--no-cache-dir", source]
    with console.status("[bc]Downloading and installing the new version…[/]", spinner="dots"):
        proc = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    if proc.returncode != 0:
        tail = "\n".join(proc.stdout.strip().splitlines()[-5:])
        console.print(f"  [err]✗ update failed[/]\n[muted]{tail}[/]")
    return proc.returncode == 0


def restart(argv: List[str]) -> None:
    """Start the freshly installed bcagent with the same arguments."""
    env = dict(os.environ, BCAGENT_UPDATED_FROM=__version__)
    cmd = [sys.executable, "-m", "bcagent"] + list(argv)
    sys.stdout.flush()
    if os.name == "nt":  # exec on Windows does not replace the console process cleanly
        sys.exit(subprocess.call(cmd, env=env))
    os.execve(sys.executable, cmd, env)


def maybe_update(cfg: dict, argv: List[str], interactive: bool) -> None:
    """Called at start-up. Updates and restarts when a newer version is available."""
    old = os.environ.pop("BCAGENT_UPDATED_FROM", None)
    if old:
        console.print(f"[ok]✓[/] bcagent updated: v{old} → [bc]v{__version__}[/]")
        return
    if os.environ.get("BCAGENT_NO_UPDATE") or not cfg.get("auto_update", True) or cfg.get("pinned_version"):
        return
    latest = latest_version()
    if not newer(latest):
        return
    if not interactive or not is_managed():
        how = "bcagent update" if is_managed() else "the installer again (or pip install --upgrade)"
        err_console.print(f"[warn]⬆ bcagent v{latest} is available[/] [muted](you have v{__version__}) – "
                          f"run {how}[/]")
        return
    console.print(f"[bc]⬆[/] A new bcagent is available: v{__version__} → [bc]v{latest}[/]. Updating…")
    if install(source_for(latest)):
        restart(argv)
    console.print(f"  [muted]Keeping v{__version__} for now – run [bc]bcagent update[/] to try again.[/]")


UPDATED = 3  # update_command() result when a new version was installed


def _set_pin(version: Optional[str]) -> None:
    from .config import load_config, save_config
    cfg = load_config()
    if version:
        cfg["pinned_version"] = version
    else:
        cfg.pop("pinned_version", None)
    save_config(cfg)


def update_command(args: Optional[List[str]] = None) -> int:
    """`bcagent update [--to X.Y.Z | --list]` / `/update`: upgrade (or roll back) now.
    Returns 0 (nothing to do), 1 (error) or UPDATED."""
    args = list(args or [])
    if args and args[0] in ("--list", "list"):
        with console.status("[bc]Listing releases…[/]", spinner="dots"):
            versions = list_releases()
        if not versions:
            console.print("[err]✗ could not list releases (offline?)[/]")
            return 1
        for v in versions:
            mark = " [ok]← installed[/]" if v == __version__ else ""
            console.print(f"  v{v}{mark}")
        console.print("[muted]Install one with[/] [bc]bcagent update --to <version>[/]")
        return 0
    target = None
    if args and args[0] in ("--to", "to") and len(args) > 1:
        target = args[1].lstrip("v")
    elif args and re.fullmatch(r"v?\d+(\.\d+){1,2}", args[0]):
        target = args[0].lstrip("v")
    if target:
        if target == __version__:
            console.print(f"[ok]✓[/] v{target} is already installed")
            _set_pin(target)
            return 0
        console.print(f"[bc]⬇[/] Installing bcagent v{target} (you have v{__version__})")
        if not install(source_for(target)):
            console.print(f"  [muted]Is v{target} a release? See[/] [bc]bcagent update --list[/]")
            return 1
        _set_pin(target)
        console.print(f"[ok]✓[/] bcagent v{target} installed. Automatic updates are paused; run "
                      "[bc]bcagent update[/] to go back to the latest version.")
        return UPDATED
    _set_pin(None)
    with console.status("[bc]Checking for a new version…[/]", spinner="dots"):
        latest = latest_version(force=True)
    if not latest:
        console.print("[err]✗ could not reach GitHub to check for updates[/]")
        return 1
    if not newer(latest):
        console.print(f"[ok]✓[/] bcagent v{__version__} is up to date")
        return 0
    console.print(f"[bc]⬆[/] Updating bcagent v{__version__} → [bc]v{latest}[/]")
    if not is_managed():
        console.print("  [warn]this bcagent was not installed by the bcagent installer – updating the "
                      f"Python environment it runs in ({sys.prefix})[/]")
    if not install(source_for(latest)):
        return 1
    console.print(f"[ok]✓[/] bcagent v{latest} installed")
    return UPDATED
