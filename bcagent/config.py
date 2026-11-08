"""Paths and persistent configuration (~/.bcagent/config.json)."""
from __future__ import annotations

import json
import os
from pathlib import Path

HOME = Path(os.environ.get("BCAGENT_HOME", str(Path.home() / ".bcagent")))
CONFIG_FILE = HOME / "config.json"
AGENTS_DIR = HOME / "agents"
HISTORY_FILE = HOME / "history"

# Smallest + fastest instruction-tuned code model available for Ollama (~400 MB).
DEFAULT_MODEL = "qwen2.5-coder:0.5b"

DEFAULTS = {
    "agent": "bulldog",      # active agent
    "ollama_host": None,     # None -> $OLLAMA_HOST or http://127.0.0.1:11434
    "num_ctx": 8192,         # model context window (tokens)
    "max_tokens": 4096,      # max tokens generated per answer
    "max_repairs": 2,        # automatic self-fix attempts after syntax errors
    "agents": {},            # installed agents {name: {...}}
}


def ensure_home() -> None:
    HOME.mkdir(parents=True, exist_ok=True)
    AGENTS_DIR.mkdir(parents=True, exist_ok=True)


def load_config() -> dict:
    ensure_home()
    cfg = json.loads(json.dumps(DEFAULTS))
    if CONFIG_FILE.exists():
        try:
            cfg.update(json.loads(CONFIG_FILE.read_text(encoding="utf-8")))
        except (OSError, ValueError):
            pass
    return cfg


def save_config(cfg: dict) -> None:
    ensure_home()
    CONFIG_FILE.write_text(json.dumps(cfg, indent=2), encoding="utf-8")
