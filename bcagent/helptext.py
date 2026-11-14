"""Built-in help: `/help`, `/help <topic>`, `bcagent help [topic]`."""
from __future__ import annotations

from typing import Dict, List, Tuple

from rich.markup import escape
from rich.table import Table

from . import ui
from .ui import console

INTRO = """[bold]bcagent[/] – Bellevue College Agent. Type what you want in plain English and press [bc]enter[/].
bcagent reads your project, writes the code, shows you the change and saves it only when you say yes.
Everything runs on your computer."""

EXAMPLES = """[bold]Try[/]
  [bc]create a tic-tac-toe game in tictactoe.py[/]
  [bc]add input validation to @calc.py[/]          [muted](@ includes a file; tab completes the name)[/]
  [bc]why does @grades.py print None?[/]
  [bc]read data.csv with pandas and plot the average score per week[/]
  [bc]commit and push[/]  ·  [bc]run the tests[/]  ·  [bc]what changed?[/]   [muted](bcagent does these itself)[/]"""

# (group, [(command, description)])
GROUPS: List[Tuple[str, List[Tuple[str, str]]]] = [
    ("Basics", [
        ("/help [topic]", "this help · /help <topic> for details (list at the bottom)"),
        ("/undo", "undo the last file changes"),
        ("/clear", "forget the conversation and start fresh"),
        ("/resume", "continue an earlier conversation in this folder (or start with bcagent -c)"),
        ("/auto", "auto-accept edits on/off (same as shift+tab)"),
        ("/exit", "leave bcagent (also exit, quit, ctrl+d)"),
    ]),
    ("Files and running code", [
        ("@file / @folder/", "include files in the request"),
        ("!command", "run a shell command, e.g. !python app.py (the output is shared with the agent)"),
        ("/run [file.py] [args]", "run a Python file (default: the last one bcagent wrote)"),
        ("/test [pytest args]", "run the tests; offers to fix failures in your code and runs them again"),
        ("/add /drop /files", "always include a file in the context / stop / list"),
        ("/init", "create BCAGENT.md – notes the agent reads with every request"),
        ("/packages", "check the built-in libraries (numpy, pandas, scikit-learn, statsmodels, statsformula)"),
    ]),
    ("Learning", [
        ("/learn [on|off]", "learning mode: hints and explanations instead of finished code"),
        ("/review [@file …]", "instructor-style feedback on your code, without rewriting it"),
        ("/export [file.md]", "save a log of this session (to hand in with an assignment)"),
        ("/course", "the rules your instructor set (course profile)"),
    ]),
    ("Agents and models", [
        ("/agents · /agents catalog", "installed agents · agents you can install"),
        ("/agent <name>", "switch agent, e.g. /agent explainer"),
        ("/install <name|model|file.md|url>", "install an agent, e.g. /install bulldog-3b"),
        ("/model <ollama-tag>", "use another Ollama model for this session"),
    ]),
    ("Tools (MCP)", [
        ("/mcp · /mcp catalog", "configured servers · public servers you can add"),
        ("/mcp add <name> · /mcp remove <name>", "add / remove a server, e.g. /mcp add fetch"),
        ("/mcp tools", "start the servers and list their tools"),
    ]),
    ("Git and GitHub", [
        ("/git", "status: branch, changed files, commits to push"),
        ("/git commit [message]", "commit everything (bcagent writes the message if you leave it out)"),
        ("/git push · /git pull", "send / get commits"),
        ("/git branch · switch · merge", "work with branches"),
        ("/git revert · /git uncommit", "undo a commit (safe after pushing) · undo the last one before pushing"),
        ("/git diff · log · discard · stash · remote · init · clone", "and any other git command"),
        ("/github login · ssh · status", "sign in (browser, token or SSH key – guided)"),
        ("/github create · pr · prs · open", "put this folder on GitHub · open a pull request"),
    ]),
    ("Notebooks and Google Colab", [
        ("@notebook.ipynb", "Jupyter notebooks are edited cell by cell; outputs of unchanged cells are kept"),
        ("/colab <url>", "link a Google Colab notebook; approved edits are saved to Colab"),
        ("/colab pull · push · open · list · unlink · login", "sync and manage linked notebooks"),
    ]),
    ("bcagent itself", [
        ("/status · /doctor", "current agent and model · check the installation"),
        ("/update [--to <version> | --list]", "update or roll back bcagent"),
        ("/bug [what happened]", "report a problem on GitHub (you see the report first)"),
    ]),
]

SHORTCUTS = [("↑/↓ enter (or a number)", "choose an option when bcagent asks"),
             ("Chat about this", "answer a question with your own words instead"),
             ("shift+tab", "auto-accept edits on/off"),
             ("esc then enter", "new line in your request"),
             ("ctrl+c", "stop the current answer (at the prompt: leave)"),
             ("tab after @", "complete a file name")]

TOPICS: Dict[str, Tuple[str, str]] = {
    "start": ("Getting started", """Start bcagent in a project folder: it only reads and writes files inside it.

  [bc]mkdir hello && cd hello && bcagent[/]

Ask for something:  [bc]create temps.py that converts Celsius to Fahrenheit[/]
bcagent shows the new code as a diff and asks [bold]Do you want to create temps.py?[/] – pick [bc]1. Yes[/].
It then offers to run the program. Change it with  [bc]make @temps.py also convert back[/].
Undo with [bc]/undo[/]. Tomorrow, [bc]bcagent -c[/] continues the conversation.

From the terminal:
  [bc]bcagent "create a todo app in todo.py"[/]       start with a request
  [bc]bcagent -p "write fizzbuzz in fizz.py" --yes[/]  one-shot: do it, print, exit
  [bc]bcagent -C ~/projects/lab3[/]                    work on another folder"""),

    "prompts": ("Writing good requests", """Say what you want, which file, and anything important – like you would to a classmate.

  [bc]add input validation to @calc.py so it rejects letters[/]
  [bc]why does @grades.py crash with KeyError on line 12?[/]
  [bc]write pytest tests for @utils.py[/]
  [bc]fix load_data in @big.py[/]   [muted]naming the function lets bcagent edit just that part of a long file[/]

[bold]@file[/] includes a file ([bold]@folder/[/] a whole folder), [bold]!cmd[/] runs a command and shares its output.
Keep requests small: one change at a time works best with small local models.
When bcagent asks a question you can always pick [bold]Chat about this[/] and type an answer instead,
for example "use a while loop instead"."""),

    "actions": ("Plain-language actions", """bcagent recognizes common requests and does them itself (it asks first):

  [bc]commit my changes[/] / [bc]commit and push[/]     → /git commit → /git push
  [bc]pull the latest changes[/]                  → /git pull
  [bc]what changed?[/]                            → /git
  [bc]undo the last commit[/]                     → /git revert
  [bc]put this on github[/]                       → /github create
  [bc]open a pull request[/]                      → /github pr
  [bc]set up an ssh key[/] / [bc]sign in to github[/]   → /github ssh / /github login
  [bc]run the tests[/]                            → /test
  [bc]review my code[/]                           → /review
  [bc]save it to colab[/]                         → /colab push

Mixed requests ("fix the bug in calc.py and commit") go to the agent first, then bcagent offers the
commit. Questions ("how do I push?") always go to the agent."""),

    "agents": ("Agents", """An agent = a model (the AI) + instructions (its job) + an edit format (how it changes files).

  [bc]/agents[/]                 installed agents          [bc]/agents catalog[/]   ready-made agents
  [bc]/agent explainer[/]        switch agent              [bc]/install bulldog-3b[/] install one
  [bc]bcagent agents recommend[/]  the best agent for this computer's memory
  [bc]bcagent agents use bulldog-3b[/]  make it the default
  [bc]bcagent -a tester "write tests for @calc.py"[/]  use one agent for one session

Catalog: bulldog (default, 0.5B), bulldog-1.5b (best in the benchmark: 40/40), bulldog-3b, bulldog-7b, tester,
explainer (explains, never edits), docs (docstrings and READMEs) and more.

Create your own: [bc]bcagent agents create statsdog[/] writes ~/.bcagent/agents/statsdog.md:
  ---
  name: statsdog
  model: qwen2.5-coder:1.5b
  edit_format: whole        [muted]# whole | diff | none (chat only)[/]
  ---
  You help students with statistics. Use pandas and statsmodels formulas. Explain the output.
Share it with a class: commit it as .bcagent/agents/statsdog.md in the project."""),

    "mcp": ("MCP tools", """MCP (Model Context Protocol) plugs extra tools into the agent – like USB for AI tools:
web pages, the time, a memory, a database, or functions you write yourself.

  [bc]/mcp catalog[/]              public servers: fetch, time, git, filesystem, memory, ...
  [bc]/mcp add fetch[/]            add one (fetch needs uv, others Node.js)
  [bc]/mcp tools[/]                list the tools
  [bc]bcagent mcp add grades -- python grades_server.py[/]   your own server
  [bc]bcagent mcp add git --project[/]   share it with the repository (.mcp.json)

When the agent wants a tool, bcagent shows the call and asks before running it.
Tip: tools need a capable model – use bulldog-3b or bigger ([bc]bcagent -a bulldog-3b[/]).
A 10-line Python server example is in examples/grades_server.py."""),

    "git": ("Git", """Git keeps the history of your project (commits are snapshots you can go back to).

  [bc]/git init[/]               start a repository (adds a Python .gitignore)
  [bc]/git[/]                    status
  [bc]/git diff[/] · [bc]/git log[/]     what changed · history
  [bc]/git commit[/]             commit everything; bcagent writes the message, you approve or edit it
  [bc]/git commit "Fix the menu"[/]   with your own message
  [bc]/git push[/] · [bc]/git pull[/]    send / get commits (bcagent shows what it will push and asks)
  [bc]/git branch new-idea[/]    create a branch and switch to it · [bc]/git switch main[/] · [bc]/git merge new-idea[/]
  [bc]/git revert[/]             undo the last commit with a new commit (safe after pushing)
  [bc]/git uncommit[/]           undo the last commit but keep the changes (only before pushing)
  [bc]/git discard file.py[/]    throw away uncommitted changes ([bc]all[/] for everything)
  [bc]/git stash[/] · [bc]/git stash pop[/] · [bc]/git remote <url>[/] · [bc]/git clone owner/repo[/]
  [bc]/git <anything>[/]         any other git command, e.g. /git tag v1.0

Files that look like secrets (.env, keys) are left out of commits unless you confirm."""),

    "github": ("GitHub", """  [bc]/github login[/]     sign in: GitHub CLI (browser) or a token (bcagent opens the page for you)
  [bc]/github ssh[/]       set up an SSH key for git@github.com addresses (creates, uploads and tests it)
  [bc]/github status[/]    who you are signed in as, SSH status, this repository
  [bc]/github create[/]    create a private GitHub repository for this folder and push it ([bc]--public[/] to share)
  [bc]/github pr[/]        push the current branch and open a pull request
  [bc]/github prs[/]       open pull requests · [bc]/github open[/] the repository in the browser

A failed push because SSH isn't set up? bcagent offers the SSH setup or a switch to HTTPS, then retries."""),

    "notebooks": ("Jupyter notebooks and Google Colab", """[bold]Jupyter[/]: mention the notebook – [bc]add a cell to @lab.ipynb that plots the age histogram[/].
bcagent shows it to the agent as Python with "# %%" cells and saves a real notebook back: unchanged
cells keep their outputs, /undo restores the exact file, and bcagent warns if cells would be removed.

[bold]Google Colab[/]: [bc]/colab https://colab.research.google.com/drive/…[/] links the notebook (bcagent helps you
sign in to Google the first time). Ask for changes as usual; each approved edit is saved to Colab –
reload the Colab tab to see it.
  [bc]/colab pull[/] get Colab's version · [bc]/colab push[/] upload · [bc]/colab open[/] · [bc]/colab list[/] · [bc]/colab unlink[/]
Notebooks on GitHub or view-only: in Colab choose File ▸ Save a copy in Drive and link the copy."""),

    "learn": ("Learning tools", """  [bc]/learn[/]              learning mode on/off: explanations, hints and a guiding question – no finished
                      code and no file edits (long code blocks in answers are hidden)
  [bc]/review @file.py[/]    feedback like an instructor's: what works, bugs, style, tests, next step
  [bc]/test[/]               run pytest; on failures bcagent fixes YOUR code (never the tests) and reruns
  [bc]/export[/]             save a log of the session – prompts, answers, edits, commands
  [bc]/course[/]             the rules from your instructor's course profile (see /help course)"""),

    "course": ("Course profiles (instructors)", """A course profile is one file that sets how bcagent behaves for a class:

  [bc]bcagent course create[/]          writes .bcagent/course.md with every setting explained
  [bc]bcagent course show[/]            check it (also /course in a session)
  [bc]bcagent course install c.md[/]    for every folder on this computer (lab machines); also a URL
  [bc]bcagent course remove[/]          uninstall the computer-wide profile

Settings (between the --- lines):
  course, instructor          shown on the welcome screen and in every session log
  learn_mode: off|on|locked   locked = students can't turn learning mode off
  allowed_agents: a, b        only these agents; other models are refused
  default_agent: bulldog      the agent sessions start with
  allow_mcp: true|false       MCP tools on/off
  session_log: auto|off       auto = a log of every session is saved in bcagent-logs/
  exam_start / exam_end       YYYY-MM-DD HH:MM – the exam window
  exam_mode: blocked|hints    during the exam: no AI help, or only error/concept explanations
Text below the second --- is sent to the agent as notes ("students haven't learned classes yet").

Ship it in the assignment's starter files. Ready-made profiles are in examples/. It guides students;
it is not proctoring software – but logs record the profile's fingerprint (see /help logs)."""),

    "logs": ("Checking student logs (instructors)", """  [bc]bcagent logs check submissions/[/]                       one row per student
  [bc]bcagent logs check submissions/ --profile course.md[/]   compare with the profile you gave out
  [bc]bcagent logs check submissions/ --csv grades.csv[/]      where to save the table

Point it at a folder with one subfolder per student (as LMS and GitHub Classroom downloads look).
For each student: sessions, minutes, prompts, edits applied, learning mode, whether the official
profile was used (fingerprint), whether a log was edited by hand (checksum), and requests made during
the exam window. Flags are a reason to ask the student, not proof."""),

    "bench": ("Benchmarking models", """  [bc]bcagent bench[/]                                      score the models of your installed agents
  [bc]bcagent bench --models qwen2.5-coder:0.5b,qwen3.5:2b[/]  compare models
  [bc]bcagent bench --pull[/]                               download missing models first
  [bc]bcagent bench --tasks fizzbuzz,big_file --repeat 3[/] · [bc]bcagent bench --list[/]

20 typical student tasks (write, fix, data, tests), each run through bcagent's normal agent loop in a
fresh folder and checked automatically by running the code. Shows the pass rate and speed per model;
results are saved in ~/.bcagent/bench/."""),

    "data": ("Data science", """Code bcagent runs can always use [bold]numpy, pandas, scikit-learn, statsmodels and statsformula[/].

  [bc]read sales.csv with pandas and print the average revenue per month[/]
  [bc]fit a regression of price on size and rooms with statsmodels and print the summary[/]
  [bc]use statsformula to build the design matrix for "y ~ x + C(region)"[/]

statsformula: [bc]import statsformula as sf; y, X = sf.dmatrices("y ~ x + C(group)", df)[/]
"No module named X" when running code? bcagent offers to install it. [bc]/packages[/] checks the libraries."""),

    "safety": ("Safety and privacy", """- The model runs on your computer; your code is not sent anywhere unless you push it or use online tools.
- You approve every file change and command (unless auto-accept is on); [bc]/undo[/] reverts the last change.
- bcagent never writes outside the folder you started it in.
- Long files: bcagent edits only the functions you name and never lets a partial rewrite delete code.
- Forgotten imports (json, pd, …) are added automatically.
- Secret-looking files aren't committed without asking; tokens are kept in ~/.bcagent only."""),

    "settings": ("Settings", """~/.bcagent/config.json:
  agent · num_ctx (how much the model sees, tokens) · max_tokens · max_repairs · ollama_host
  default_packages · learn_mode · bug_reports · auto_update · pinned_version
Environment: BCAGENT_HOME · BCAGENT_LEARN=1 · BCAGENT_NO_UPDATE=1 · BCAGENT_NO_PACKAGES=1 ·
GH_TOKEN / GITHUB_TOKEN · BCAGENT_GOOGLE_TOKEN · OLLAMA_HOST

Updates: bcagent updates itself from tested releases. [bc]bcagent update --list[/] · [bc]bcagent update --to 1.6.0[/]
(go back; pauses automatic updates) · [bc]bcagent update[/] (latest again)."""),

    "troubleshooting": ("Troubleshooting", """  [bc]bcagent doctor[/]                    checks Python, Ollama, the model, git, GitHub, libraries
  "Ollama is not running"           start the Ollama app or run [bc]ollama serve[/]
  wrong or weak answers             mention files with @, ask for one thing at a time,
                                    try a bigger agent: [bc]bcagent agents recommend[/]
  "the file is too long"            name the function: "fix load_data in @big.py"
  "No module named …"               accept the install offer, or [bc]bcagent packages[/]
  "Permission denied (publickey)"   [bc]/github ssh[/]
  "pytest failed to start"          create a project venv: python -m venv .venv, then pip install pytest
  anything else                     [bc]/bug what happened[/]"""),
}

ALIASES = {"commands": "", "getting-started": "start", "begin": "start", "/git": "git", "/github": "github",
           "/colab": "notebooks", "colab": "notebooks", "jupyter": "notebooks", "ipynb": "notebooks",
           "/mcp": "mcp", "tools": "mcp", "/agent": "agents", "/agents": "agents", "agent": "agents",
           "/learn": "learn", "/review": "learn", "/test": "learn", "/export": "learn", "/course": "course",
           "instructors": "course", "instructor": "course", "profile": "course", "exam": "course",
           "/logs": "logs", "/bench": "bench", "benchmark": "bench", "models": "agents", "/model": "agents",
           "libraries": "data", "packages": "data", "/packages": "data", "privacy": "safety",
           "config": "settings", "update": "settings", "/update": "settings", "problems": "troubleshooting",
           "/bug": "troubleshooting", "bug": "troubleshooting", "requests": "prompts", "/resume": "start",
           "shortcuts": "", "keys": ""}


def show(topic: str = "") -> None:
    topic = topic.strip().lower()
    topic = ALIASES.get(topic, topic)
    if topic in TOPICS:
        title, body = TOPICS[topic]
        console.print(f"[bold]{title}[/]\n")
        console.print(body)
        console.print(f"\n[muted]All topics: {', '.join(TOPICS)} – /help for the overview[/]")
        return
    if topic:
        console.print(f"[warn]No help topic '{escape(topic)}'.[/] Topics: {', '.join(TOPICS)}\n")
    console.print(INTRO + "\n")
    console.print(EXAMPLES + "\n")
    for group, rows in GROUPS:
        console.print(f"[bold]{group}[/]")
        t = Table.grid(padding=(0, 2))
        t.add_column(style="bc", no_wrap=True, min_width=30)
        t.add_column()
        for cmd, desc in rows:
            t.add_row("  " + escape(cmd), desc)
        console.print(t)
    console.print("[bold]Shortcuts[/]")
    t = Table.grid(padding=(0, 2))
    t.add_column(style="bc", no_wrap=True, min_width=30)
    t.add_column()
    for k, d in SHORTCUTS:
        t.add_row("  " + escape(k), d)
    console.print(t)
    console.print(f"\n[bold]More help:[/] [bc]/help <topic>[/] – {', '.join(TOPICS)}\n"
                  "[muted]From the terminal: bcagent help <topic> · bcagent --help · full guide: "
                  "https://github.com/PedroBSB/Bellevue-College-Agent-Capstone[/]")


EPILOG = """commands:
  bcagent                               interactive session in this folder
  bcagent "request"                     start with a request    · bcagent -p "request" --yes   one-shot
  bcagent -c / -r                       continue the last / pick an earlier conversation
  bcagent help [topic]                  help (topics: start, prompts, actions, agents, mcp, git, github,
                                        notebooks, learn, course, logs, bench, data, safety, settings,
                                        troubleshooting)
  bcagent agents list|catalog|recommend|install|create|use|remove
  bcagent mcp list|catalog|add|remove|tools
  bcagent git <command> · bcagent github login|ssh|status|create|pr|prs|open
  bcagent colab login|logout|list|link|pull|push
  bcagent course show|create|install <file|url>|remove      (instructors)
  bcagent logs check <folder> [--profile f] [--csv f]       (instructors)
  bcagent bench [--models a,b] [--tasks …] [--pull]          score models on student tasks
  bcagent packages [list] · bcagent doctor · bcagent bug "…"
  bcagent update [--to X.Y.Z | --list]

examples:
  bcagent "create a todo app in todo.py"
  bcagent -a tester "write tests for @calc.py"
  bcagent --learn
  bcagent --colab https://colab.research.google.com/drive/… "add a cell that plots x"
  bcagent course create && bcagent course show"""
