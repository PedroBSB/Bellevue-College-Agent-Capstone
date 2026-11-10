# bcagent — Bellevue College Agent (Capstone)

<p align="center">
  <img src="assets/bcagent-logo.svg" alt="bcagent: Bellevue College Agent, the multicolor Bulldog logo" width="520">
</p>

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
| Python 3.9+ (if missing) | Homebrew, or the official python.org `.pkg`; **no admin:** private Python in `~/.bcagent` | apt / dnf / yum / pacman / zypper / apk; **no sudo:** private Python in `~/.bcagent` | winget, or the python.org installer (per user); **blocked:** portable Python in `%USERPROFILE%\.bcagent\python` |
| bcagent + requirements (`rich`, `prompt_toolkit`) | venv in `~/.bcagent/venv` | venv in `~/.bcagent/venv` | venv in `%USERPROFILE%\.bcagent\venv` |
| `bcagent` command on PATH | `~/.local/bin/bcagent` | `~/.local/bin/bcagent` | `%USERPROFILE%\.bcagent\bin\bcagent.cmd` |
| Ollama (if missing) | Homebrew, or Ollama.app (`~/Applications` without admin rights) | official `ollama.com/install.sh`; **no sudo:** unpacked into `~/.bcagent/ollama` | winget, or `OllamaSetup.exe`; **blocked:** portable Ollama in `%USERPROFILE%\.bcagent\ollama` |
| Default model | `ollama pull qwen2.5-coder:0.5b` | same | same |

Options: `--no-model` / `-NoModel` skips the model download (it then downloads on first run).

**No administrator rights?** (lab and school computers, accounts without `sudo`.) The installer
checks whether it can use `sudo`. If it can't, or if a system install fails, it installs whatever is
missing for your user only:

- **Python:** a private copy in `~/.bcagent`, installed with [uv](https://docs.astral.sh/uv/) or, if
  that fails, a prebuilt [python-build-standalone](https://github.com/astral-sh/python-build-standalone)
  build. The same fallback is used when the system Python can't create virtual environments (for
  example Ubuntu without `python3-venv`). On Windows the fallback is the portable NuGet Python.
- **Ollama:** on macOS in `~/Applications/Ollama.app`, on Linux in `~/.bcagent/ollama`, linked from
  `~/.local/bin/ollama`. On Windows a portable `ollama.exe` goes in `%USERPROFILE%\.bcagent\ollama`.

To skip `sudo` even on an administrator account, run `./install.sh --user`.
`--uninstall` / `-Uninstall`, or `uninstall.sh` / `uninstall.bat`, removes bcagent (keeps settings, Ollama and models).

To remove **everything** (bcagent, settings, agents, MCP servers, the models and Ollama), use the
uninstall-all option. It asks for confirmation first; add `--yes` / `-Yes` to skip the question.
Python is left installed.

```bash
./uninstall.sh --all          # or: ./install.sh --uninstall-all
```
```bat
uninstall.bat --all           & rem or: install.ps1 -UninstallAll
```

## Updates

bcagent keeps itself up to date. When it starts, it compares its version with the one on GitHub. It
checks at most every 6 hours, and a check takes at most 3 seconds, so being offline doesn't slow it
down. If a newer version exists, bcagent upgrades itself and restarts with the same arguments. Run
`bcagent update` (or `/update` in a session) to check right away. To turn automatic updates off, set
`"auto_update": false` in `~/.bcagent/config.json` or set `BCAGENT_NO_UPDATE=1`. If bcagent wasn't
installed with the bcagent installer, it only tells you that a new version is available.

## Use

```bash
cd my-project
bcagent                                   # interactive session, like `claude`
bcagent "create a todo list app in todo.py"   # start the session with a prompt
bcagent -p "write fizzbuzz in fizz.py" --yes  # one-shot / scripting mode, like `claude -p`
bcagent -a tester "write tests for @calc.py"  # use a specific agent
bcagent -m qwen2.5-coder:1.5b                 # use any Ollama model for this run
bcagent mcp add fetch                         # add a public MCP server (see below)
bcagent doctor                                # check the installation
bcagent update                                # update bcagent now (it also updates itself)
```

bcagent works on the folder you start it in (or `-C <folder>`): it can read every file there, and it
creates and changes files inside that folder after you approve the diff. It never writes outside it.

Each request goes through these steps:

1. bcagent sends the prompt with the project's file tree, `BCAGENT.md` notes, and the relevant files.
2. The local model streams its answer to the terminal.
3. bcagent turns the answer into file edits and shows them as a colored diff. Choose
   **Yes / Yes, don't ask again / No / Chat about this** with the **↑/↓ arrow keys** and `enter`
   (or press the option's number, `y`, `n` or `esc`). **Chat about this** lets you type a reply
   to clarify the request or ask for a different change. It is offered every time bcagent asks
   you something.
4. After writing a `.py` file, bcagent compiles it. On a syntax error it asks the model to fix the
   file, up to 2 times by default.
5. bcagent offers to run the Python scripts it writes, even when the model didn't suggest a command,
   and it asks before running any command. Python code runs with the project's own `.venv` if there
   is one. Otherwise it uses a private environment (`~/.bcagent/runenv`), so `python` and `pip install`
   work even on machines without a system Python or where `pip` is blocked. The output goes back to
   the agent, and if the script fails you can send the error to the model to fix. Run a file any time
   with `/run file.py`.

### Inside the session

| Input | Action |
|-------|--------|
| `@file.py` / `@folder/` | tag a file or folder: its content is sent to the agent (`@` + tab completes, like Claude Code) |
| `!command` | run a shell command; its output goes to the agent with your next prompt |
| `↑` / `↓`, `enter` | pick an option when bcagent asks (or press its number); **Chat about this** to reply instead |
| `shift+tab` | toggle auto-accept edits |
| `esc` then `enter` | new line |
| `quit`, `exit`, `ctrl+c`, `ctrl+d` | leave bcagent |
| `ctrl+c` while it is answering | stop the current answer |
| `/help` | all commands |
| `/agents`, `/agents catalog` | list installed / installable agents |
| `/agent <name>` | switch agent |
| `/install <name\|model\|file.md\|url>` | install an agent |
| `/model <ollama-tag>` | change the model for this session |
| `/add`, `/drop`, `/files` | pin files in context |
| `/init` | create `BCAGENT.md` project notes (like `CLAUDE.md`) |
| `/mcp`, `/mcp catalog`, `/mcp add <name>`, `/mcp tools` | MCP servers |
| `/run [file.py] [args]` | run a Python file (default: the last one bcagent wrote) |
| `/undo` | undo the last edits |
| `/update` | check for a new bcagent version, install it and restart |
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

## MCP servers

bcagent can install public [Model Context Protocol](https://modelcontextprotocol.io) servers and lets the
agent call their tools (each call asks for your approval unless auto-accept is on). The config file uses
the same `mcpServers` format as Claude Code / Claude Desktop.

```bash
bcagent mcp catalog                         # public servers: filesystem, fetch, git, time, memory, ...
bcagent mcp add fetch                       # from the catalog
bcagent mcp add @modelcontextprotocol/server-memory    # any npm package (runs with npx)
bcagent mcp add pypi:mcp-server-time        # any PyPI package (runs with uvx)
bcagent mcp add weather -e API_KEY=123 -- python weather_server.py   # any command
bcagent mcp add git --project               # save in ./.mcp.json to share with the repo
bcagent mcp list                            # configured servers
bcagent mcp tools                           # start them and list their tools
bcagent mcp remove fetch
```

User servers live in `~/.bcagent/mcp.json`, project servers in `./.mcp.json`. Only stdio servers are
supported. npm servers need Node.js and PyPI servers need [uv](https://docs.astral.sh/uv/).
Small models such as the default 0.5B one rarely use tools well; a larger agent like `bulldog-3b`
works better with MCP.

## Project layout

```
bcagent/
├── install.sh / install.ps1 / install.bat    installers (macOS+Linux / Windows)
├── uninstall.sh / uninstall.bat
├── pyproject.toml
└── bcagent/
    ├── cli.py        argument parsing, REPL, slash commands, `agents`, `mcp` and `doctor` subcommands
    ├── session.py    the agent loop: prompt → model → edits → approval → check → repair
    ├── edits.py      parses the model's answer into file edits and shell commands
    ├── project.py    file tree, BCAGENT.md memory, choosing relevant files for context
    ├── agents.py     built-in agent, catalog, installing custom agents
    ├── mcp.py        MCP client, public server catalog, ~/.bcagent/mcp.json
    ├── llm.py        Ollama client (Python standard library only, streaming)
    ├── ui.py         Bellevue College Blue theme, Bulldog banner, diffs, menus
    ├── update.py     self-update: checks GitHub for new versions and upgrades in place
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
