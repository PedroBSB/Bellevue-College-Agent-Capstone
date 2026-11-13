"""Pick the best agent this computer can run comfortably, based on its memory (RAM).

bcagent never switches agents by itself: it suggests one (once) and installs it only if you say yes.
"""
from __future__ import annotations

import ctypes
import os
import platform
import subprocess
import time
from typing import Optional, Tuple

from .ui import console

# (minimum RAM in GB, agent, download size) – the model should use well under half the memory
TIERS = [(24, "bulldog-7b", "4.7 GB"), (12, "bulldog-3b", "1.9 GB"), (6, "bulldog-1.5b", "1 GB"),
         (0, "bulldog", "400 MB")]
ASK_AGAIN = 7 * 24 * 3600   # "Not now" asks again after a week


def total_ram_gb() -> Optional[float]:
    try:
        system = platform.system()
        if system == "Darwin":
            out = subprocess.run(["sysctl", "-n", "hw.memsize"], capture_output=True, text=True, timeout=5).stdout
            return int(out.strip()) / 1024 ** 3
        if system == "Linux":
            with open("/proc/meminfo", encoding="utf-8") as f:
                for line in f:
                    if line.startswith("MemTotal:"):
                        return int(line.split()[1]) / 1024 ** 2
        if system == "Windows":
            class MEMORYSTATUSEX(ctypes.Structure):
                _fields_ = [("dwLength", ctypes.c_ulong), ("dwMemoryLoad", ctypes.c_ulong),
                            ("ullTotalPhys", ctypes.c_ulonglong), ("ullAvailPhys", ctypes.c_ulonglong),
                            ("ullTotalPageFile", ctypes.c_ulonglong), ("ullAvailPageFile", ctypes.c_ulonglong),
                            ("ullTotalVirtual", ctypes.c_ulonglong), ("ullAvailVirtual", ctypes.c_ulonglong),
                            ("sullAvailExtendedVirtual", ctypes.c_ulonglong)]
            stat = MEMORYSTATUSEX()
            stat.dwLength = ctypes.sizeof(MEMORYSTATUSEX)
            if ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(stat)):  # type: ignore[attr-defined]
                return stat.ullTotalPhys / 1024 ** 3
    except (OSError, ValueError, subprocess.SubprocessError, AttributeError):
        return None
    if hasattr(os, "sysconf"):
        try:
            return os.sysconf("SC_PAGE_SIZE") * os.sysconf("SC_PHYS_PAGES") / 1024 ** 3
        except (ValueError, OSError):
            return None
    return None


def recommend(ram: Optional[float] = None, allowed=None) -> Tuple[str, str]:
    """(agent name, download size) for this computer."""
    ram = total_ram_gb() if ram is None else ram
    for need, name, size in TIERS:
        if (ram or 0) >= need and (not allowed or name in allowed):
            return name, size
    return "bulldog", "400 MB"


def describe(ram: Optional[float] = None) -> str:
    ram = total_ram_gb() if ram is None else ram
    name, size = recommend(ram)
    return f"{name} ({size} download)" + (f" for {ram:.0f} GB of RAM" if ram else "")


def maybe_suggest(session, cfg: dict, client) -> None:
    """At start-up: if this computer can run a better agent than the default one, offer it (once)."""
    from . import ui
    from .agents import all_agents
    from .config import save_config
    if session.print_mode or not ui.is_interactive() or session.agent.name != "bulldog":
        return
    asked = cfg.get("recommend_asked", 0)
    if asked is True or (asked and time.time() - float(asked) < ASK_AGAIN):
        return
    allowed = session.course.allowed_agents if session.course else None
    ram = total_ram_gb()
    name, size = recommend(ram, allowed)
    if name == "bulldog":
        return
    have = all_agents(session.project.root).get(name)
    what = "It's already installed. Use it as your default?" if have else \
        f"({size} download, one time). Install it and make it your default?"
    c = ui.choose(f"This computer has {ram:.0f} GB of RAM, enough for [bc]{name}[/], which writes better code than "
                  f"the default bulldog. {what}",
                  [f"Yes, use {name}", "Not now", "No, don't ask again"], cancel=1)
    cfg["recommend_asked"] = True if c != 1 else time.time()
    if c == 0:
        from .cli import install_agent, ensure_model
        agent = have if have and ensure_model(client, have.model) else install_agent(client, name)
        if agent:
            cfg["agent"] = name
            session.agent = agent
            console.print(f"[ok]✓[/] now using [bc]{name}[/]. Switch back any time with [bc]/agent bulldog[/].")
    save_config(cfg)
