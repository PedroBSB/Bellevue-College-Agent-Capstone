"""The agent loop: prompt -> local model -> proposed edits -> approval -> apply -> self-check -> repair."""
from __future__ import annotations

import ast
import hashlib
import json
import time
from datetime import datetime
import os
import random
import re
import shutil
import subprocess
import shlex
import sys
from pathlib import Path
from typing import List, Optional, Tuple

from rich.live import Live
from rich.markdown import Markdown
from rich.markup import escape
from rich.text import Text

from . import packages as packages_mod, ui
from .agents import Agent
from .config import HOME
from .colab import ColabError, ColabSync, check_source
from .edits import Edit, check_python, parse_response, raw_text, valid_path, write_edit
from .llm import Ollama
from .mcp import MCPError, MCPManager, parse_tool_calls
from .project import Project
from .ui import console

LEARN_RULES = """LEARNING MODE: the user is a student learning to program. Help them learn instead of doing the work.
- Do NOT write the complete solution, whole functions or whole files, and never use `FILE:` lines.
- Explain the idea in plain words, point to where the problem is (file and line), and give the next step as a hint.
- Ask one guiding question the student can answer to make progress.
- Code examples must be tiny (at most 5 lines) and show a concept, not the answer to their task.
- Be encouraging. If they ask for the full answer, give a bigger hint and suggest how to check their own solution."""

REVIEW_PROMPT = """Review this code like a friendly computer science instructor reviewing a student's work.
Do not rewrite the files. Use these sections:
1. What works well (1-3 points)
2. Bugs or problems (file and line, why it is wrong, how to fix it in words)
3. Style and readability (PEP 8, names, comments, docstrings)
4. Testing: which cases to test
5. One concrete next step
Files to review: {files}"""

SESSIONS_DIR = HOME / "sessions"
PYTEST_SUMMARY = re.compile(r"\b\d+ (?:passed|failed|errors?|skipped|xfailed|deselected)\b|no tests ran")

VERBS = ["Fetching", "Sniffing out bugs", "Bulldogging", "Chewing on it", "Wagging", "Digging",
         "Pawing through code", "Thinking", "Herding bytes", "Barking at the compiler"]


class Session:
    def __init__(self, project: Project, agent: Agent, cfg: dict, client: Ollama,
                 auto: bool = False, print_mode: bool = False):
        self.project = project
        self.agent = agent
        self.cfg = cfg
        self.client = client
        self.auto = auto                # auto-accept file edits
        self.print_mode = print_mode    # -p: non-interactive
        self.history: List[dict] = []
        self.notes: List[str] = []      # e.g. output of "!cmd" to share with the model
        self.undo_stack: List[List[Tuple[str, Optional[str]]]] = []
        self.mcp = MCPManager(project.root)
        self.trusted_tools: set = set()  # MCP tools the user allowed for the whole session
        self.last_python: Optional[str] = None  # last .py file bcagent created or changed (for /run)
        self.colab = ColabSync(project.root)    # Colab notebooks linked with /colab
        self.learn = bool(cfg.get("learn_mode")) or bool(os.environ.get("BCAGENT_LEARN"))  # hints, no edits
        self.no_edits = False                   # one turn without edits (/review)
        self.last_error = None                  # (exception, input) of the last internal error, for /bug
        self.protect_tests = False              # while fixing failing tests: don't let the model edit tests
        self.events: List[dict] = []            # session log for /export
        self.started = datetime.now()
        self.session_id = self.started.strftime("%Y%m%d-%H%M%S")

    # -- modes ----------------------------------------------------------------
    @property
    def edit_format(self) -> str:
        return "none" if (self.learn or self.no_edits) else self.agent.edit_format

    def log(self, kind: str, text: str, **extra) -> None:
        self.events.append({"time": datetime.now().strftime("%H:%M:%S"), "kind": kind, "text": text, **extra})

    def start_mcp(self) -> None:
        """Connect to the configured MCP servers (first turn only)."""
        if self.mcp.started or not self.mcp.servers():
            return
        if self.print_mode:
            self.mcp.start()
        else:
            with console.status("[bc]Connecting MCP servers…[/]", spinner="dots",
                                spinner_style=ui.BC_BLUE_LIGHT) as st:
                self.mcp.start(lambda n: st.update(f"[bc]Connecting MCP server {n}…[/]"))
        for name, err in self.mcp.errors.items():
            ui.err_console.print(f"  [warn]⚠ MCP server {name} failed:[/] {err}")

    # -- prompt building ------------------------------------------------------
    def budget_chars(self) -> int:
        tokens = int(self.cfg.get("num_ctx", 8192)) - int(self.cfg.get("max_tokens", 4096)) // 2 - 600
        return max(4000, tokens * 3)

    def trimmed_history(self) -> List[dict]:
        out = []
        for m in self.history[-8:]:
            c = m["content"]
            if len(c) > 2500:
                c = c[:2500] + "\n... (truncated)"
            out.append({"role": m["role"], "content": c})
        return out

    def build_messages(self, prompt: str) -> List[dict]:
        hist = self.trimmed_history()
        used = sum(len(m["content"]) for m in hist)
        ctx, included = self.project.context(prompt, self.budget_chars() - used)
        if included and not self.print_mode:
            for f in included:
                console.print(f"  [muted]⎿  Read[/] [bc]{f}[/]")
        notes = ""
        if self.notes:
            notes = "\n\n".join(self.notes) + "\n\n"
            self.notes.clear()
        hint = ""
        if self.edit_format != "none":
            targets = [f for f in dict.fromkeys(self.project.mentioned(prompt) + self.project.named(prompt))]
            if targets:
                hint = ("\n\nChange the existing file(s) " + ", ".join(targets) +
                        " (shown above). Do not create other files unless the task asks for it.")
            notebooks = [f for f in dict.fromkeys(targets + self.project.pinned)
                         if self.colab.is_mirror(f) or f.lower().endswith(".ipynb")]
            if notebooks:
                kind = "Google Colab" if self.colab.is_mirror(notebooks[0]) else "Jupyter"
                hint += ("\n" + ", ".join(notebooks) + f" is a {kind} notebook. Each cell starts with a "
                         "`# %%` line; text cells start with `# %% [markdown]` and every line is commented "
                         "with `# `. Keep the cell lines, add new cells with `# %%`, and use `!pip install` "
                         "for packages. Change the notebook by writing the whole file as `FILE: " + notebooks[0] +
                         "`." + (" bcagent uploads it to Colab after the user approves." if kind != "Jupyter" else ""))
            big = [f for f in targets + self.project.pinned if f in self.project.truncated]
            if big and self.edit_format == "whole":
                hint += ("\nIMPORTANT: " + ", ".join(dict.fromkeys(big)) + " is too long and was only partly "
                         "shown. Do not rewrite it with FILE:. Explain the change and show only the lines "
                         "to change.")
            elif self.edit_format == "whole":
                hint += "\nAnswer with `FILE: <path>` followed by the COMPLETE updated file in a code block."
        user = f"{ctx}\n\n{notes}Task: {prompt}{hint}"
        if self.learn or self.no_edits:
            system = Agent(self.agent.name, self.agent.model, edit_format="none",
                           instructions=self.agent.instructions).system_prompt()
            if self.learn:
                system += "\n\n" + LEARN_RULES
        else:
            system = self.agent.system_prompt()
        libs = packages_mod.wanted(self.cfg)
        if libs:
            system += "\n\n" + packages_mod.PROMPT_NOTE.format(
                libs=", ".join("scikit-learn (import sklearn)" if l == "scikit-learn" else l for l in libs))
        tools = self.mcp.prompt_section()
        if tools:
            system += "\n\n" + tools
        return [{"role": "system", "content": system}] + hist + \
               [{"role": "user", "content": user}]

    def options(self) -> dict:
        return {"temperature": self.agent.temperature, "num_ctx": int(self.cfg.get("num_ctx", 8192)),
                "num_predict": int(self.cfg.get("max_tokens", 4096))}

    # -- generation -----------------------------------------------------------
    def generate(self, messages: List[dict]) -> Tuple[str, bool]:
        buf: List[str] = []
        interrupted = False
        gen = self.client.chat_stream(self.agent.model, messages, self.options())
        try:
            if self.print_mode:
                for tok in gen:
                    buf.append(tok)
            else:
                verb = random.choice(VERBS)
                with console.status(f"[bc]{verb}…[/] [muted](ctrl+c to interrupt)[/]", spinner="dots",
                                    spinner_style=ui.BC_BLUE_LIGHT):
                    for tok in gen:
                        buf.append(tok)
                        if "".join(buf).strip():
                            break
                height = max(6, console.height - 6)
                with Live(console=console, refresh_per_second=12, transient=True) as live:
                    for tok in gen:
                        buf.append(tok)
                        tail = "\n".join("".join(buf).split("\n")[-height:])
                        live.update(Text(tail, style="muted"))
        except KeyboardInterrupt:
            interrupted = True
            gen.close()
        text = "".join(buf).strip()
        if self.print_mode:
            print(text)
        else:
            ui.assistant(text)
            s = self.client.last_stats
            if s.get("eval_count") and s.get("eval_duration"):
                tps = s["eval_count"] / (s["eval_duration"] / 1e9)
                console.print(f"  [muted]⎿  {s['eval_count']} tokens · {tps:.0f} tok/s · "
                              f"{self.agent.model} (local)[/]")
        return text, interrupted

    # -- main turn ------------------------------------------------------------
    def turn(self, prompt: str, repair: int = 0, tool_rounds: int = 0) -> None:
        self.start_mcp()
        text, interrupted = self.generate(self.build_messages(prompt))
        self.history += [{"role": "user", "content": prompt}, {"role": "assistant", "content": text}]
        self.log("prompt" if not (repair or tool_rounds) else "follow-up", prompt)
        self.log("answer", text, model=self.agent.model)
        self.save()
        if interrupted:
            console.print("  [warn]⎿  Interrupted by user[/]")
            return
        calls = parse_tool_calls(text) if self.mcp.clients else []
        if calls and tool_rounds < 5:
            results = self.run_tools(calls)
            if results:
                return self.turn("Tool results:\n" + "\n\n".join(results) +
                                 "\n\nContinue the original task using these results.", repair, tool_rounds + 1)
        if self.edit_format == "none":
            return
        edits, commands, orphans = parse_response(text, self.project)
        if not edits and orphans:
            e, chat = self.adopt_orphan(orphans, prompt)
            if chat:
                return self.turn(chat)
            if e:
                edits = [e]
        blocked = self.guard(edits)
        applied, feedback = self.review(edits)
        if feedback:
            return self.turn(feedback)
        problems = [f"- {e.path}: {e.error}" for e in edits if e.error and not e.blocked]
        broken = set()
        for path in applied:
            err = self.check(path)
            if err:
                problems.append(f"- {path}: {err}")
                broken.add(path)
        self.sync_colab([p for p in applied if p not in broken])
        self.report_blocked(blocked)
        if problems:
            for p in problems:
                console.print(f"  [err]✗[/] {p[2:]}")
            if repair < int(self.cfg.get("max_repairs", 2)):
                console.print(f"  [warn]⎿  Asking {self.agent.name} to fix it "
                              f"(attempt {repair + 1}/{self.cfg.get('max_repairs', 2)})…[/]")
                fix = ("Your previous answer has problems:\n" + "\n".join(problems) +
                       "\nFix them. Write the COMPLETE corrected file(s) using `FILE: path` and a code block.")
                return self.turn(fix, repair + 1)
        self.offer_commands(commands + self.run_suggestions(applied, commands, problems))

    # -- safety: never let a whole-file rewrite delete code the model didn't see ---------
    def guard(self, edits: List[Edit]) -> List[Edit]:
        blocked = []
        for e in edits:
            if self.protect_tests and is_test_file(e.path) and not e.error:
                e.error, e.blocked = ("it is a test file – while fixing failing tests bcagent changes your code, "
                                      "not the tests (edit the test yourself if it is wrong)"), True
                blocked.append(e)
                continue
            if e.error or e.is_new or not e.whole:
                continue
            if e.path.lower().endswith(".ipynb") or self.colab.is_mirror(e.path):
                from .colab import text_to_cells
                before = sum(1 for k, _ in text_to_cells(e.old or "") if k == "code")
                after = sum(1 for k, _ in text_to_cells(e.new) if k == "code")
                if after < before:
                    e.warning = f"the model removed {before - after} code cell(s) – check the diff"
            old_lines = len((e.old or "").splitlines())
            new_lines = len(e.new.splitlines())
            reason = None
            if e.path in self.project.truncated:
                shown, total = self.project.truncated[e.path]
                reason = (f"the file is too long for {self.agent.model}'s context: the model saw only "
                          f"the first {shown} of {total} lines, so rewriting it would delete the rest")
            elif e.path not in self.project.seen and old_lines >= 20 and new_lines < old_lines * 0.5:
                reason = (f"the model never saw this file but its version has {new_lines} lines instead of "
                          f"{old_lines}, so it would delete code")
            if reason:
                e.error, e.blocked = reason, True
                blocked.append(e)
        return blocked

    def report_blocked(self, blocked: List[Edit]) -> None:
        for e in blocked:
            console.print(f"  [warn]⚠ Not applied to {escape(e.path)}:[/] {escape(e.error)}.")
            self.log("blocked", f"{e.path}: {e.error}")
        if any("too long" in (e.error or "") or "never saw" in (e.error or "") for e in blocked):
            console.print("  [muted]⎿  Options: ask about one function or section (e.g. \"fix the load_data "
                          "function in @file.py\"), use an agent that makes small edits ([/][bc]/install "
                          "bulldog-3b[/][muted]), or give the model more context: raise [/][bc]num_ctx[/][muted] in "
                          "~/.bcagent/config.json.[/]")

    def check(self, path: str) -> Optional[str]:
        if self.colab.is_mirror(path):  # notebook cells may use !pip / %magic lines
            try:
                return check_source(self.project.read(path), path)
            except OSError as e:
                return str(e)
        return check_python(self.project, path)

    # -- Colab ----------------------------------------------------------------
    def sync_colab(self, paths: List[str]) -> None:
        """Upload changed Colab notebook mirrors to Google Drive."""
        out = console if not self.print_mode else ui.err_console
        for path in paths:
            if not self.colab.is_mirror(path):
                continue
            try:
                done = self.colab.push(path, ask=not self.print_mode)
            except ColabError as e:
                out.print(f"  [err]✗ not saved to Colab:[/] {e}")
                continue
            if done:
                out.print(f"  [ok]⎿[/]  Saved to Colab: [bc]{escape(self.colab.links[path]['name'])}[/] "
                          "[muted](reload the Colab tab to see it)[/]")
            else:
                out.print(f"  [muted]⎿  Not uploaded – {path} keeps the change; /colab push uploads it[/]")

    # -- MCP tools ------------------------------------------------------------
    def run_tools(self, calls) -> List[str]:
        results = []
        for name, args in calls[:5]:
            shown = json.dumps(args, ensure_ascii=False)
            if not self.print_mode:
                console.print(f"[bc]●[/] [bold]{escape(name)}[/]([muted]{escape(shown[:200])}[/])")
            if not (self.auto or name in self.trusted_tools):
                if self.print_mode or not ui.is_interactive():
                    results.append(f"{name}: not run (needs approval, use --yes)")
                    continue
                c, msg = ui.ask(f"Allow MCP tool {escape(name)}?",
                                ["Yes", f"Yes, and don't ask again for {escape(name)} this session", "No"])
                if c == ui.CHAT:
                    results.append(f"{name}: not run. Instead the user said: {msg}")
                    continue
                if c == 2:
                    results.append(f"{name}: the user declined to run this tool")
                    continue
                if c == 1:
                    self.trusted_tools.add(name)
            try:
                out = self.mcp.call(name, args)
            except MCPError as e:
                out = f"ERROR: {e}"
            if not self.print_mode:
                first = out.strip().splitlines()[:3]
                console.print(Text("  ⎿  " + "\n     ".join(l[:110] for l in first) +
                                   (" …" if len(out.splitlines()) > 3 else ""), style="muted"))
            results.append(f"{name}({shown}) returned:\n{out[:4000]}")
        return results

    # -- edits ----------------------------------------------------------------
    def guess_target(self, prompt: str) -> Optional[str]:
        found = self.project.mentioned(prompt) + self.project.named(prompt)
        if len(set(found)) == 1:
            return found[0]
        new = [valid_path(t) for t in prompt.replace("@", " ").split()]
        new = [t for t in new if t and t.endswith(".py")]
        if len(new) == 1:
            return new[0]
        if len(self.project.pinned) == 1:
            return self.project.pinned[0]
        return None

    def adopt_orphan(self, orphans, prompt: str) -> Tuple[Optional[Edit], Optional[str]]:
        """The model wrote code without saying which file. Offer to save it.
        Returns (edit, chat message) - the message is set when the user chose "Chat about this"."""
        code = [b for b in orphans if b.lang in ("", "python", "py", "python3") and len(b.body.strip()) > 40]
        if not code:
            return None, None
        block = max(code, key=lambda b: len(b.body))
        guess = self.guess_target(prompt) or ("main.py" if not self.project.exists("main.py") else None)
        if self.print_mode or not ui.is_interactive():
            path = guess if self.auto else None
        else:
            opts = ([f"Save it as {guess}"] if guess else []) + ["Save it as another file…", "Don't save it"]
            c, msg = ui.ask("Save the generated code to a file?", opts)
            if c == ui.CHAT:
                return None, f"About the code you just wrote: {msg}"
            if opts[c].startswith("Don't"):
                path = None
            elif opts[c].startswith("Save it as another"):
                path = console.input("  [muted]file name[/] [bc]❯[/] ").strip() or None
            else:
                path = guess
        path = valid_path(path) if path else None
        if not path:
            return None, None
        old = self.project.read(path) if self.project.exists(path) else None
        return Edit(path, new=block.body.rstrip("\n") + "\n", old=old, whole=True), None

    def review(self, edits: List[Edit]) -> Tuple[List[str], Optional[str]]:
        applied: List[str] = []
        batch: List[Tuple[str, Optional[str]]] = []
        feedback = None
        for e in edits:
            if e.error:
                continue
            if not self.print_mode:
                ui.show_edit(e)
            if not self.auto:
                if self.print_mode or not ui.is_interactive():
                    console.print(f"  [muted]⎿  Not applied: {e.path} (use --yes to apply edits)[/]")
                    continue
                verb = "create" if e.is_new else "make this edit to"
                c, msg = ui.ask(f"Do you want to {verb} {e.path}?",
                                ["Yes", "Yes, and don't ask again this session (shift+tab)", "No"])
                if c == ui.CHAT:
                    feedback = (f"About your proposed change to {e.path} (not applied yet): {msg}\n"
                                "Answer the question or redo the change as asked.")
                    break
                if c == 1:
                    self.auto = True
                elif c == 2:
                    console.print(f"  [muted]⎿  Skipped {e.path}[/]")
                    continue
            before = raw_text(self.project, e.path)
            write_edit(self.project, e)
            batch.append((e.path, before))
            applied.append(e.path)
            self.project.seen.add(e.path)
            self.log("edit", f"{'Created' if e.is_new else 'Updated'} {e.path} (+{e.stats()[0]} -{e.stats()[1]})",
                     diff="\n".join(e.diff_lines()[:200]))
            if e.path.endswith(".py"):
                self.last_python = e.path
            a, r = e.stats()
            out = console if not self.print_mode else ui.err_console
            out.print(f"  [ok]⎿[/]  {'Created' if e.is_new else 'Updated'} [bc]{e.path}[/] "
                      f"[add]+{a}[/] [del]-{r}[/]")
        if batch:
            self.undo_stack.append(batch)
        return applied, feedback

    def undo(self) -> None:
        if not self.undo_stack:
            console.print("[muted]Nothing to undo.[/]")
            return
        for path, old in reversed(self.undo_stack.pop()):
            p = self.project.resolve(path)
            if old is None:
                if p.exists():
                    p.unlink()
                console.print(f"  [warn]↺[/] removed {path}")
            else:
                p.write_text(old, encoding="utf-8")
                console.print(f"  [warn]↺[/] restored {path}")
                if self.colab.is_mirror(path):
                    self.sync_colab([path])

    # -- commands -------------------------------------------------------------
    def offer_commands(self, commands: List[str]) -> None:
        if not commands or self.print_mode or not ui.is_interactive():
            for c in commands:
                console.print(f"  [muted]suggested command:[/] {c}")
            return
        for cmd in commands[:5]:
            c, msg = ui.ask(f"Run this command?  [bc]{escape(cmd)}[/]", ["Yes", "No"])
            if c == ui.CHAT:
                return self.turn(f"About the command `{cmd}` (not run yet): {msg}")
            if c != 0:
                continue
            if self.execute(cmd) is not None:
                return

    def execute(self, cmd: str) -> Optional[bool]:
        """Run a command, share its output with the model and offer to fix failures.
        Returns None normally, True if a follow-up turn was started."""
        code, out = run_command(cmd, str(self.project.root))
        self.notes.append(f"I ran `{cmd}` (exit code {code}). Output:\n```\n{out[-2500:]}\n```")
        self.log("command", cmd, code=code, output=out[-1500:])
        if code == 0:
            console.print("  [ok]⎿  done[/]")
            return None
        console.print(f"  [err]⎿  exit code {code}[/]")
        m = re.search(r"No module named '([\w.]+)'", out)
        if m and (self.auto or (ui.is_interactive() and not self.print_mode)):
            pkg = packages_mod.pip_name_for(m.group(1))
            if self.auto or ui.choose(f"The module [bold]{escape(m.group(1))}[/] is not installed. Install "
                                      f"[bc]{escape(pkg)}[/] with pip and run again?", ["Yes", "No"]) == 0:
                if run_command(f"python -m pip install {pkg}", str(self.project.root))[0] == 0:
                    self.notes.pop()
                    return self.execute(cmd)
        if self.print_mode or not ui.is_interactive():
            return None
        c, msg = ui.ask("Ask bcagent to fix this error?", ["Yes", "No"])
        if c == 1:
            return None
        extra = f"\n{msg}" if c == ui.CHAT else "\nFix the code."
        self.notes.pop()
        self.turn(f"I ran `{cmd}` and it failed with exit code {code}:\n```\n{out[-3000:]}\n```{extra}")
        return True

    def run_suggestions(self, applied: List[str], commands: List[str], problems: List[str]) -> List[str]:
        """Offer to run the Python scripts bcagent just wrote when the model didn't suggest it."""
        if problems:
            return []
        out = []
        for path in applied:
            if self.colab.is_mirror(path) or path.lower().endswith(".ipynb"):
                continue  # notebooks run in Colab / Jupyter, not here
            if any(path in c or Path(path).name in c for c in commands):
                continue
            if is_runnable_script(self.project.resolve(path)):
                out.append(python_command(path))
        return out[:2]

    def run_file(self, arg: str) -> None:
        """/run [file.py] [args]: run a Python file (default: the last one bcagent wrote)."""
        parts = shlex.split(arg, posix=os.name != "nt") if arg else []
        target = parts[0] if parts else self.last_python
        if not target:
            console.print("[muted]Nothing to run yet – use [bc]/run file.py[/][/]")
            return
        if not self.project.exists(target):
            console.print(f"[err]✗ {escape(target)} not found[/]")
            return
        cmd = python_command(target, parts[1:]) if target.endswith(".py") else " ".join([target] + parts[1:])
        self.execute(cmd)

    # -- /test -----------------------------------------------------------------
    def run_tests(self, arg: str = "", rounds: int = 3) -> bool:
        """Run pytest; on failures offer to let the agent fix the code, then run the tests again."""
        cmd = "python -m pytest -q" + (f" {arg}" if arg else "")
        extra: dict = {}
        for attempt in range(rounds + 2):
            code, out = run_command(cmd, str(self.project.root), extra)
            self.log("command", cmd, code=code, output=out[-1500:])
            if code not in (0, 5) and not PYTEST_SUMMARY.search(out) and "No module named pytest" not in out:
                # pytest itself didn't start (broken plugin, bad conftest...) – not a bug in the user's code
                if not extra:
                    console.print("  [warn]⎿  pytest failed to start – trying again without installed pytest "
                                  "plugins[/]")
                    extra = {"PYTEST_DISABLE_PLUGIN_AUTOLOAD": "1"}
                    continue
                console.print("  [err]⎿  pytest could not start, so the tests didn't run.[/] [muted]This is a "
                              "problem with the Python environment, not your code. Create a project "
                              "environment ([/][bc]python -m venv .venv[/][muted] then [/][bc]pip install pytest[/]"
                              "[muted]) – bcagent uses .venv automatically.[/]")
                return False
            if code == 0:
                console.print("  [ok]⎿  all tests passed[/]")
                return True
            interactive = ui.is_interactive() and not self.print_mode
            if "No module named pytest" in out:
                if interactive and ui.choose("pytest is not installed. Install it?", ["Yes", "No"]) != 0:
                    return False
                if not interactive and not self.auto:
                    return False
                run_command("python -m pip install pytest", str(self.project.root))
                continue
            if code == 5:  # no tests collected
                console.print("  [warn]⎿  no tests found[/]")
                if interactive and ui.choose("Ask bcagent to write tests?", ["Yes", "No"]) == 0:
                    self.turn("Write pytest unit tests for the code in this project. Put them in "
                              "tests/test_<module>.py and cover normal cases, edge cases and errors.")
                    continue
                return False
            console.print(f"  [err]⎿  tests failed (exit code {code})[/]")
            if attempt == rounds or self.edit_format == "none":
                return False
            if interactive:
                c, msg = ui.ask("Ask bcagent to fix the failing tests?", ["Yes", "No"])
                if c == 1:
                    return False
                note = f"\n{msg}" if c == ui.CHAT else ""
            elif self.auto:
                note = ""
            else:
                return False
            targets = self.tested_modules(out)
            where = (" ".join("@" + t for t in targets) + " so that the tests pass") if targets else \
                "the code so that the tests pass"
            self.protect_tests = True
            try:
                self.turn(f"The tests fail. Fix {where}. Do NOT change the test files. "
                          f"pytest output:\n```\n{out[-3500:]}\n```{note}")
            finally:
                self.protect_tests = False
        return False

    def tested_modules(self, output: str) -> List[str]:
        """Project modules imported by the failing test files (the files to fix)."""
        tests = dict.fromkeys(re.findall(r"([\w./-]*test[\w./-]*\.py)", output))
        found: List[str] = []
        for t in tests:
            if not self.project.exists(t):
                continue
            for mod in re.findall(r"^\s*(?:from|import)\s+([\w.]+)", self.project.read(t), re.M):
                rel = mod.replace(".", "/") + ".py"
                for cand in (rel, "src/" + rel):
                    if self.project.exists(cand) and not is_test_file(cand) and cand not in found:
                        found.append(cand)
        return found[:4]

    # -- /review ---------------------------------------------------------------
    def review_code(self, arg: str = "") -> None:
        files = [f.lstrip("@") for f in arg.split()]
        if not files:
            files = self.changed_files() or ([self.last_python] if self.last_python else []) or self.project.pinned
        files = [f for f in files if self.project.exists(f) or (self.project.root / f).is_dir()]
        if not files:
            console.print("[muted]Nothing to review – name a file: [/][bc]/review @calc.py[/]")
            return
        console.print("[muted]Reviewing " + ", ".join(files) + "…[/]")
        self.no_edits = True
        try:
            self.turn(REVIEW_PROMPT.format(files=" ".join("@" + f for f in files)))
        finally:
            self.no_edits = False

    def changed_files(self) -> List[str]:
        """Uncommitted Python/notebook files according to git (empty if not a git repo)."""
        try:
            r = subprocess.run(["git", "status", "--porcelain", "-uall"], cwd=self.project.root,
                               capture_output=True, text=True, timeout=10)
        except (OSError, subprocess.SubprocessError):
            return []
        if r.returncode != 0:
            return []
        out = [l[3:].strip('"') for l in r.stdout.splitlines() if l.strip()]
        return [f for f in out if f.endswith((".py", ".ipynb")) and self.project.exists(f)][:6]

    # -- /export: a readable log of the session (for instructors) ----------------
    def export(self, arg: str = "") -> Path:
        from . import __version__
        name = arg.strip() or f"bcagent-session-{self.session_id}.md"
        if not name.endswith(".md"):
            name += ".md"
        path = self.project.resolve(name)
        prompts = sum(1 for e in self.events if e["kind"] == "prompt")
        edits = sum(1 for e in self.events if e["kind"] == "edit")
        lines = [f"# bcagent session log",
                 "",
                 f"- Date: {self.started.strftime('%Y-%m-%d %H:%M')} – {datetime.now().strftime('%H:%M')}",
                 f"- Project: {self.project.root.name}",
                 f"- bcagent v{__version__} · agent {self.agent.name} · model {self.agent.model} (local)",
                 f"- Learning mode: {'on' if self.learn else 'off'}",
                 f"- {prompts} prompt(s), {edits} file edit(s) applied",
                 ""]
        for e in self.events:
            t, kind, text = e["time"], e["kind"], e["text"]
            if kind in ("prompt", "follow-up"):
                lines += [f"## {t} · {'You' if kind == 'prompt' else 'bcagent follow-up'}", "", text.strip(), ""]
            elif kind == "answer":
                lines += [f"**bcagent** ({e.get('model', '')}):", "", text.strip() or "_(no answer)_", ""]
            elif kind == "edit":
                lines += [f"**Edit applied:** {text}", ""]
                if e.get("diff"):
                    lines += ["```diff", e["diff"], "```", ""]
            elif kind == "command":
                lines += [f"**Ran** `{text}` → exit code {e.get('code')}", ""]
                if e.get("output"):
                    lines += ["```", e["output"].strip()[-1200:], "```", ""]
            else:
                lines += [f"**{kind}:** {text}", ""]
        path.write_text("\n".join(lines), encoding="utf-8")
        return path

    # -- saved sessions (/resume, bcagent -c) -------------------------------------
    def _project_key(self) -> str:
        return hashlib.sha1(str(self.project.root).encode()).hexdigest()[:12]

    def save(self) -> None:
        if not self.history:
            return
        first = next((e["text"] for e in self.events if e["kind"] == "prompt"), "")
        data = {"root": str(self.project.root), "agent": self.agent.name, "model": self.agent.model,
                "history": self.history[-40:], "pinned": self.project.pinned, "events": self.events[-300:],
                "learn": self.learn, "title": first[:80], "saved": time.time(), "id": self.session_id}
        d = SESSIONS_DIR / self._project_key()
        try:
            d.mkdir(parents=True, exist_ok=True)
            (d / f"{self.session_id}.json").write_text(json.dumps(data), encoding="utf-8")
            for old in sorted(d.glob("*.json"))[:-10]:  # keep the 10 most recent per project
                old.unlink()
        except OSError:
            pass

    def saved_sessions(self) -> List[dict]:
        d = SESSIONS_DIR / self._project_key()
        out = []
        for f in sorted(d.glob("*.json"), reverse=True) if d.is_dir() else []:
            try:
                data = json.loads(f.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            if data.get("id") != self.session_id:
                out.append(data)
        return out

    def restore(self, data: dict) -> None:
        self.history = data.get("history", [])
        self.events = data.get("events", []) + self.events
        self.project.pinned = [p for p in data.get("pinned", []) if self.project.exists(p)]
        self.learn = data.get("learn", self.learn)
        self.session_id = data.get("id", self.session_id)
        last = next((m["content"] for m in reversed(self.history) if m["role"] == "user"), "")
        console.print(f"[ok]✓[/] resumed the session from "
                      f"{datetime.fromtimestamp(data.get('saved', 0)).strftime('%b %d %H:%M')} "
                      f"({len(self.history) // 2} exchange(s)). Last request: [bc]{escape(last[:100])}[/]")


def is_test_file(path: str) -> bool:
    name = Path(path).name
    return name.startswith("test_") or name.endswith("_test.py") or name == "conftest.py" or \
        "tests/" in path.replace("\\", "/")


def is_runnable_script(path: Path) -> bool:
    """True for a .py file that does something when run (not just a module of defs, not a test file)."""
    if path.suffix != ".py" or path.name.startswith("test_") or path.name.endswith("_test.py") \
            or path.name in ("__init__.py", "setup.py", "conftest.py"):
        return False
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"))
    except (OSError, SyntaxError, ValueError):
        return False
    quiet = (ast.Import, ast.ImportFrom, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef,
             ast.Assign, ast.AnnAssign)
    for node in tree.body:
        if isinstance(node, quiet):
            continue
        if isinstance(node, ast.Expr) and isinstance(node.value, ast.Constant):
            continue  # docstring
        return True
    return False


def _quote(arg: str) -> str:
    if os.name == "nt":
        return f'"{arg}"' if (" " in arg or not arg) else arg
    return shlex.quote(arg)


def python_command(path: str, args: Optional[List[str]] = None) -> str:
    return " ".join(["python", _quote(path)] + [_quote(a) for a in (args or [])])


def _usable(exe: Optional[str]) -> bool:
    # On Windows "python.exe" in WindowsApps is the Microsoft Store stub, not a real Python.
    return bool(exe) and "WindowsApps" not in exe


def _venv_python(venv: Path) -> Optional[Path]:
    for exe in (venv / "bin" / "python", venv / "Scripts" / "python.exe"):
        if exe.is_file():
            return exe
    return None


def run_env() -> Optional[Path]:
    """bcagent's own environment for running the code it writes (~/.bcagent/runenv).

    It is created on first use from the Python that runs bcagent, so it works without sudo, without a
    system Python and on systems where `pip install` is blocked (PEP 668 "externally-managed").
    Packages installed system-wide stay visible (--system-site-packages)."""
    from .config import HOME
    venv = HOME / "runenv"
    exe = _venv_python(venv)
    if exe:
        return exe
    console.print("  [muted]⎿  Setting up a Python environment for running your code (one time)…[/]")
    try:
        subprocess.run([sys.executable, "-m", "venv", "--system-site-packages", str(venv)],
                       check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=300)
    except (OSError, subprocess.SubprocessError):
        shutil.rmtree(venv, ignore_errors=True)
        return None
    return _venv_python(venv)


def python_for(cwd: str) -> str:
    """The Python to run user code with: the project's own venv, then bcagent's run environment,
    then the system Python, then the Python that runs bcagent itself."""
    root = Path(cwd)
    for venv in (".venv", "venv", "env"):
        exe = _venv_python(root / venv)
        if exe:
            return str(exe)
    exe = run_env()
    if exe:
        return str(exe)
    for name in ("python3", "python"):
        found = shutil.which(name)
        if _usable(found):
            return found
    return sys.executable


def _fix_command(cmd: str, cwd: str = ".") -> str:
    """Run python/pip commands with a Python that really exists and can install packages."""
    head, _, rest = cmd.partition(" ")
    low = head.lower()
    if low in ("python", "python3", "python.exe", "py"):
        if low == "py" and rest.startswith(("-3", "-2")):
            rest = rest[2:].lstrip()
        return f"{_quote(python_for(cwd))} {rest}".strip()
    if low in ("pip", "pip3", "pip.exe"):
        return f"{_quote(python_for(cwd))} -m pip {rest}".strip()
    return cmd


def run_command(cmd: str, cwd: str, extra_env: Optional[dict] = None) -> Tuple[int, str]:
    console.print(f"[bc]$[/] {escape(cmd)}", highlight=False)
    cmd = _fix_command(cmd, cwd)
    env = dict(os.environ, PYTHONUNBUFFERED="1", **(extra_env or {}))
    if re.match(r"\s*(git|gh)\b", cmd) or "&& git " in cmd:
        from .git import auth_env
        env = auth_env(env)  # GitHub token for push / pull / clone, never shown in the command
    proc = subprocess.Popen(cmd, shell=True, cwd=cwd, stdout=subprocess.PIPE,
                            stderr=subprocess.STDOUT, env=env)
    out = bytearray()
    try:
        fd = proc.stdout.fileno()
        while True:
            chunk = os.read(fd, 4096)
            if not chunk:
                break
            out.extend(chunk)
            sys.stdout.write(chunk.decode(errors="replace"))
            sys.stdout.flush()
        code = proc.wait()
    except KeyboardInterrupt:
        proc.terminate()
        code = proc.wait()
        out.extend(b"\n^C interrupted")
    return code, out.decode(errors="replace")
