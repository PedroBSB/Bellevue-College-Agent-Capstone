"""Agents = a local model + a persona + an edit format.

Sources (later ones override earlier ones with the same name):
  1. the built-in default agent "bulldog"
  2. agents installed with `bcagent agents install` (stored in ~/.bcagent/config.json)
  3. Markdown agent files in ~/.bcagent/agents/*.md
  4. project agents in ./.bcagent/agents/*.md
"""
from __future__ import annotations

import re
import shutil
import urllib.request
from dataclasses import asdict, dataclass, fields
from pathlib import Path
from typing import Optional

from .config import AGENTS_DIR, DEFAULT_MODEL, ensure_home, load_config, save_config

IDENTITY = """You are bcagent, the Bellevue College coding agent (mascot: the Bulldog).
You run in the user's terminal inside their project folder. You write correct, clean, working Python code."""

WHOLE_RULES = """How to answer:
- First say in one or two short sentences what you will do.
- To create or change a file, write a line `FILE: <relative/path>` and right after it the COMPLETE file content in a fenced code block.
- Always write the whole file. Never write "rest of the code unchanged" or leave parts out.
- Only output the files you create or change.
- To suggest a terminal command (for example to run the program), put it in a ```bash block.
- Prefer the Python standard library. Add short comments and an `if __name__ == "__main__":` entry point for scripts.

Example answer:
I will create a greeting script.

FILE: hello.py
```python
def main():
    print("Hello, Bulldogs!")


if __name__ == "__main__":
    main()
```

```bash
python hello.py
```"""

DIFF_RULES = """How to answer:
- First say in one or two short sentences what you will do.
- To create a NEW file write a line `FILE: <relative/path>` followed by the complete file in a fenced code block.
- To change an EXISTING file write `FILE: <relative/path>` followed by a fenced code block with one or more SEARCH/REPLACE blocks:

FILE: app.py
```python
<<<<<<< SEARCH
exact lines copied from the current file
=======
new lines
>>>>>>> REPLACE
```
- The SEARCH part must match the existing file exactly. Keep blocks small.
- To suggest a terminal command put it in a ```bash block.
- Prefer the Python standard library and write clean, commented code."""

CHAT_RULES = """You only explain code and answer questions. Never create or modify files and never use `FILE:` lines.
Use short paragraphs, bullet points and small code examples."""


@dataclass
class Agent:
    name: str
    model: str = DEFAULT_MODEL
    description: str = ""
    instructions: str = ""
    edit_format: str = "whole"   # whole | diff | none
    temperature: float = 0.2
    source: str = "builtin"

    def system_prompt(self) -> str:
        rules = {"whole": WHOLE_RULES, "diff": DIFF_RULES, "none": CHAT_RULES}.get(self.edit_format, WHOLE_RULES)
        parts = [IDENTITY, rules]
        if self.instructions.strip():
            parts.append("Special instructions for this agent:\n" + self.instructions.strip())
        return "\n\n".join(parts)


DEFAULT_AGENT = Agent(
    "bulldog", DEFAULT_MODEL,
    "Default - smallest & fastest local Python coder (Qwen2.5-Coder 0.5B, ~400 MB)",
)

CATALOG = {a.name: a for a in [
    Agent("bulldog-1.5b", "qwen2.5-coder:1.5b", "Smarter, still fast (Qwen2.5-Coder 1.5B, ~1 GB)", source="catalog"),
    Agent("bulldog-3b", "qwen2.5-coder:3b", "Good quality, targeted edits (Qwen2.5-Coder 3B, ~1.9 GB)",
          edit_format="diff", source="catalog"),
    Agent("bulldog-7b", "qwen2.5-coder:7b", "Best quality, needs 8+ GB RAM (Qwen2.5-Coder 7B, ~4.7 GB)",
          edit_format="diff", source="catalog"),
    Agent("deepseek-1.3b", "deepseek-coder:1.3b", "DeepSeek-Coder 1.3B instruct (~780 MB)", source="catalog"),
    Agent("yi-coder-1.5b", "yi-coder:1.5b", "Yi-Coder 1.5B chat (~870 MB)", source="catalog"),
    Agent("granite-3b", "granite-code:3b", "IBM Granite Code 3B instruct (~2 GB)", source="catalog"),
    Agent("tester", DEFAULT_MODEL, "Writes pytest unit tests for your code", source="catalog",
          instructions="You write pytest unit tests. Put tests in tests/test_<module>.py, import the "
                       "module under test, and cover normal cases, edge cases and errors."),
    Agent("explainer", DEFAULT_MODEL, "Explains code and answers questions, never edits files",
          edit_format="none", source="catalog",
          instructions="Explain code clearly for a college student. Use short sections and examples."),
    Agent("docs", DEFAULT_MODEL, "Adds docstrings, type hints and README files", source="catalog",
          instructions="Add Google-style docstrings and type hints and write README files. "
                       "Never change program behavior."),
]}

_FIELDS = {f.name for f in fields(Agent)}


def slug(text: str) -> str:
    return re.sub(r"[^a-z0-9._-]+", "-", text.lower()).strip("-") or "agent"


def parse_markdown_agent(text: str, source: str) -> Agent:
    meta: dict = {}
    body = text
    m = re.match(r"^---\s*\n(.*?)\n---\s*\n?(.*)$", text, re.S)
    if m:
        for line in m.group(1).splitlines():
            if ":" in line:
                k, v = line.split(":", 1)
                v = re.sub(r"\s+#.*$", "", v)  # allow trailing comments
                meta[k.strip().lower()] = v.strip().strip("\"'")
        body = m.group(2)
    try:
        temperature = float(meta.get("temperature", 0.2))
    except ValueError:
        temperature = 0.2
    return Agent(
        name=slug(meta.get("name") or Path(source).stem),
        model=meta.get("model") or DEFAULT_MODEL,
        description=meta.get("description", ""),
        instructions=body.strip(),
        edit_format=meta.get("edit_format") or meta.get("format") or "whole",
        temperature=temperature,
        source=source,
    )


def all_agents(project_root: Optional[Path] = None) -> dict:
    agents = {"bulldog": DEFAULT_AGENT}
    for name, data in load_config().get("agents", {}).items():
        clean = {k: v for k, v in data.items() if k in _FIELDS and k not in ("name", "source")}
        agents[name] = Agent(name=name, source="installed", **clean)
    dirs = [AGENTS_DIR]
    if project_root:
        dirs.append(Path(project_root) / ".bcagent" / "agents")
    for d in dirs:
        if d.is_dir():
            for f in sorted(d.glob("*.md")):
                try:
                    a = parse_markdown_agent(f.read_text(encoding="utf-8"), str(f))
                    agents[a.name] = a
                except OSError:
                    pass
    return agents


def install(spec: str, name: Optional[str] = None, model: Optional[str] = None,
            instructions: Optional[str] = None, edit_format: Optional[str] = None,
            description: Optional[str] = None) -> Agent:
    """Install an agent from the catalog, an Ollama model tag, a local .md file or a URL to a .md file."""
    ensure_home()
    if spec.startswith(("http://", "https://")):
        with urllib.request.urlopen(spec, timeout=30) as r:
            text = r.read().decode("utf-8")
        agent = parse_markdown_agent(text, spec)
        if name:
            agent.name = slug(name)
        (AGENTS_DIR / f"{agent.name}.md").write_text(text, encoding="utf-8")
        agent.source = str(AGENTS_DIR / f"{agent.name}.md")
        return agent
    if spec.lower().endswith(".md") and Path(spec).is_file():
        agent = parse_markdown_agent(Path(spec).read_text(encoding="utf-8"), spec)
        if name:
            agent.name = slug(name)
        dest = AGENTS_DIR / f"{agent.name}.md"
        shutil.copyfile(spec, dest)
        agent.source = str(dest)
        return agent

    if spec in CATALOG:
        base = CATALOG[spec]
        agent = Agent(**{k: getattr(base, k) for k in _FIELDS})
    else:  # any Ollama model tag, e.g. "codellama:7b" or "qwen2.5-coder:1.5b"
        agent = Agent(name=slug(spec.replace(":", "-")), model=spec,
                      description=f"Custom agent using Ollama model {spec}")
    if name:
        agent.name = slug(name)
    if model:
        agent.model = model
    if instructions is not None:
        agent.instructions = instructions
    if edit_format:
        agent.edit_format = edit_format
    if description:
        agent.description = description
    agent.source = "installed"
    cfg = load_config()
    data = asdict(agent)
    data.pop("name")
    data.pop("source")
    cfg.setdefault("agents", {})[agent.name] = data
    save_config(cfg)
    return agent


def remove(name: str) -> str:
    if name == "bulldog":
        raise ValueError("the default 'bulldog' agent cannot be removed")
    cfg = load_config()
    if name in cfg.get("agents", {}):
        del cfg["agents"][name]
        if cfg.get("agent") == name:
            cfg["agent"] = "bulldog"
        save_config(cfg)
        return "config"
    f = AGENTS_DIR / f"{name}.md"
    if f.exists():
        f.unlink()
        return str(f)
    raise ValueError(f"no installed agent named '{name}'")


TEMPLATE = """---
name: {name}
description: What this agent is good at
model: {model}
edit_format: whole        # whole (full files, best for tiny models) | diff | none (chat only)
temperature: 0.2
---
Write the special instructions for this agent here.
For example: "You build Flask web apps. Always include a requirements.txt."
"""


def create_template(name: str, model: str = DEFAULT_MODEL) -> Path:
    ensure_home()
    path = AGENTS_DIR / f"{slug(name)}.md"
    if not path.exists():
        path.write_text(TEMPLATE.format(name=slug(name), model=model), encoding="utf-8")
    return path
