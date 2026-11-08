# bcagent — Bellevue College Agent (Capstone)

```
   ,-.             ,-.
  ( ( `.  _____  .' ) )      bcagent
   \ \  \/     \/  / /       Bellevue College Agent
    `.\  _     _  /,'        a Claude Code style CLI that writes
      | (o)   (o) |          Python code with a tiny LOCAL LLM
      |    ___    |
     /|   (___)   |\
    | \ \_______/ / |
     \ '.\/\/\/\/.' /
      '.__/~~~\__.'
     [=o==o==o==o==o=]
```

`bcagent` is a terminal coding agent modeled on Claude Code. You type a prompt, it reads your
project, generates Python code, shows you a diff, and edits files once you approve. It runs
entirely on your machine: no API keys, no cloud, no cost.

The default agent, **bulldog**, runs **Qwen2.5-Coder 0.5B** (`qwen2.5-coder:0.5b`, about 400 MB) through
[Ollama](https://ollama.com). It's the smallest and fastest instruction-tuned code model on Ollama,
and it produces more than 100 tokens/s on a laptop CPU or GPU. You can install larger or different
agents at any time.

The interface uses **Bellevue College Blue** (`#003D79`) and the Bulldog mascot.

---

## Install

The installers set up everything that's missing: **Python 3**, **Ollama**, the `bcagent` CLI with
its Python requirements (in an isolated virtual environment), and the default model.

| OS | Command |
|----|---------|
| **macOS / Linux** | `./install.sh` |
| **Windows** | double-click `install.bat`, or run `powershell -ExecutionPolicy Bypass -File install.ps1` |

Install without cloning:

```bash
# macOS / Linux
curl -fsSL https://raw.githubusercontent.com/PedroBSB/Bellevue-College-Agent-Capstone/main/install.sh | bash
```
```powershell
# Windows PowerShell
irm https://raw.githubusercontent.com/PedroBSB/Bellevue-College-Agent-Capstone/main/install.ps1 | iex
```

What the installer does:

| Step | macOS | Linux | Windows |
|------|-------|-------|---------|
| Python 3.9+ (if missing) | Homebrew, or the official python.org `.pkg` | apt / dnf / yum / pacman / zypper / apk | winget, or the official python.org installer |
| bcagent + requirements (`rich`, `prompt_toolkit`) | venv in `~/.bcagent/venv` | venv in `~/.bcagent/venv` | venv in `%USERPROFILE%\.bcagent\venv` |
| `bcagent` command on PATH | `~/.local/bin/bcagent` | `~/.local/bin/bcagent` | `%USERPROFILE%\.bcagent\bin\bcagent.cmd` |
| Ollama (if missing) | Homebrew, or Ollama.app | official `ollama.com/install.sh` | winget, or `OllamaSetup.exe` |
| Default model | `ollama pull qwen2.5-coder:0.5b` | same | same |

Options: `--no-model` / `-NoModel` skips the model download (it then downloads on first run).
`--uninstall` / `-Uninstall`, or `uninstall.sh` / `uninstall.bat`, removes bcagent.

## Use

```bash
cd my-project
bcagent                                   # interactive session, like `claude`
bcagent "create a todo list app in todo.py"   # start the session with a prompt
bcagent -p "write fizzbuzz in fizz.py" --yes  # one-shot / scripting mode, like `claude -p`
bcagent -a tester "write tests for @calc.py"  # use a specific agent
bcagent -m qwen2.5-coder:1.5b                 # use any Ollama model for this run
bcagent doctor                                # check the installation
```

Each request goes through these steps:

1. bcagent sends the prompt with the project's file tree, `BCAGENT.md` notes, and the relevant files.
2. The local model streams its answer to the terminal.
3. bcagent turns the answer into file edits and shows them as a colored diff. You choose
   **1. Yes / 2. Yes, don't ask again / 3. No, tell bcagent what to do differently**.
4. After writing a `.py` file, bcagent compiles it. On a syntax error it asks the model to fix the
   file, up to 2 times by default.
5. If the model suggests a command (for example `python calc.py`), bcagent asks before running it.
   If the command fails, you can send the error back to the model to fix.

### Inside the session

| Input | Action |
|-------|--------|
| `@file.py` / `@folder` | add a file or folder to the context (tab completes) |
| `!command` | run a shell command; its output goes to the agent with your next prompt |
| `shift+tab` | toggle auto-accept edits |
| `esc` then `enter` | new line |
| `ctrl+c` | interrupt generation |
| `/help` | all commands |
| `/agents`, `/agents catalog` | list installed / installable agents |
| `/agent <name>` | switch agent |
| `/install <name\|model\|file.md\|url>` | install an agent |
| `/model <ollama-tag>` | change the model for this session |
| `/add`, `/drop`, `/files` | pin files in context |
| `/init` | create `BCAGENT.md` project notes (like `CLAUDE.md`) |
| `/undo` | undo the last edits |
| `/clear`, `/status`, `/doctor`, `/exit` | |

## Agents

An agent combines a local model, a set of instructions, and an edit format: `whole` writes full files
and is the most reliable format for tiny models, `diff` uses SEARCH/REPLACE blocks for larger models,
and `none` only chats.

```bash
bcagent agents list                 # installed agents
bcagent agents catalog              # ready-made agents
bcagent agents install bulldog-1.5b # from the catalog (pulls the model)
bcagent agents install codellama:7b --name llama --prompt "You write very short code"
bcagent agents install ./my-agent.md                  # from a markdown file
bcagent agents install https://example.com/agent.md   # from a URL
bcagent agents create flaskdog      # write a template in ~/.bcagent/agents/flaskdog.md
bcagent agents use bulldog-1.5b     # set the default agent
bcagent agents remove llama --delete-model
```

Catalog: `bulldog-1.5b`, `bulldog-3b`, `bulldog-7b` (Qwen2.5-Coder), `deepseek-1.3b`, `yi-coder-1.5b`,
`granite-3b`, plus three persona agents on the 0.5B model: `tester` (pytest), `explainer` (no edits),
and `docs` (docstrings and READMEs).

To share an agent with a team, commit `.bcagent/agents/<name>.md` to the repository. Agent file format:

```markdown
---
name: flaskdog
description: Builds Flask web apps
model: qwen2.5-coder:1.5b
edit_format: whole
temperature: 0.2
---
You build Flask web apps. Always include a requirements.txt and a templates/ folder.
```

## Project layout

```
bcagent/
├── install.sh / install.ps1 / install.bat    installers (macOS+Linux / Windows)
├── uninstall.sh / uninstall.bat
├── pyproject.toml
└── bcagent/
    ├── cli.py        argument parsing, REPL, slash commands, `agents` and `doctor` subcommands
    ├── session.py    the agent loop: prompt → model → edits → approval → check → repair
    ├── edits.py      parses the model's answer into file edits and shell commands
    ├── project.py    file tree, BCAGENT.md memory, choosing relevant files for context
    ├── agents.py     built-in agent, catalog, installing custom agents
    ├── llm.py        Ollama client (Python standard library only, streaming)
    ├── ui.py         Bellevue College Blue theme, Bulldog banner, diffs, menus
    └── config.py     ~/.bcagent/config.json
```

Configuration lives in `~/.bcagent/config.json`: `num_ctx`, `max_tokens`, `max_repairs`, `ollama_host`,
and the default `agent`. Set `BCAGENT_HOME` to move it.

## Requirements

- macOS 11+, Windows 10/11, or a modern Linux distribution
- About 1 GB of free disk space and 2 GB of RAM for the default agent

The installers handle everything else.

---
Bellevue College Capstone project. MIT License. The Bulldog mascot and Bellevue College Blue identify
the project as a Bellevue College student project; this is not official college software.
