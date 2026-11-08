"""The agent loop: prompt -> local model -> proposed edits -> approval -> apply -> self-check -> repair."""
from __future__ import annotations

import json
import os
import random
import shutil
import subprocess
import sys
from typing import List, Optional, Tuple

from rich.live import Live
from rich.markdown import Markdown
from rich.markup import escape
from rich.text import Text

from . import ui
from .agents import Agent
from .edits import Edit, check_python, parse_response, valid_path, write_edit
from .llm import Ollama
from .mcp import MCPError, MCPManager, parse_tool_calls
from .project import Project
from .ui import console

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
        if self.agent.edit_format != "none":
            targets = [f for f in dict.fromkeys(self.project.mentioned(prompt) + self.project.named(prompt))]
            if targets:
                hint = ("\n\nChange the existing file(s) " + ", ".join(targets) +
                        " (shown above). Do not create other files unless the task asks for it.")
            if self.agent.edit_format == "whole":
                hint += "\nAnswer with `FILE: <path>` followed by the COMPLETE updated file in a code block."
        user = f"{ctx}\n\n{notes}Task: {prompt}{hint}"
        system = self.agent.system_prompt()
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
        if interrupted:
            console.print("  [warn]⎿  Interrupted by user[/]")
            return
        calls = parse_tool_calls(text) if self.mcp.clients else []
        if calls and tool_rounds < 5:
            results = self.run_tools(calls)
            if results:
                return self.turn("Tool results:\n" + "\n\n".join(results) +
                                 "\n\nContinue the original task using these results.", repair, tool_rounds + 1)
        if self.agent.edit_format == "none":
            return
        edits, commands, orphans = parse_response(text, self.project)
        if not edits and orphans:
            e = self.adopt_orphan(orphans, prompt)
            if e:
                edits = [e]
        applied, feedback = self.review(edits)
        if feedback:
            return self.turn(feedback)
        problems = [f"- {e.path}: {e.error}" for e in edits if e.error]
        for path in applied:
            err = check_python(self.project, path)
            if err:
                problems.append(f"- {path}: {err}")
        if problems:
            for p in problems:
                console.print(f"  [err]✗[/] {p[2:]}")
            if repair < int(self.cfg.get("max_repairs", 2)):
                console.print(f"  [warn]⎿  Asking {self.agent.name} to fix it "
                              f"(attempt {repair + 1}/{self.cfg.get('max_repairs', 2)})…[/]")
                fix = ("Your previous answer has problems:\n" + "\n".join(problems) +
                       "\nFix them. Write the COMPLETE corrected file(s) using `FILE: path` and a code block.")
                return self.turn(fix, repair + 1)
        self.offer_commands(commands)

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
                c = ui.choose(f"Allow MCP tool {escape(name)}?",
                              ["Yes", f"Yes, and don't ask again for {escape(name)} this session", "No"])
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

    def adopt_orphan(self, orphans, prompt: str) -> Optional[Edit]:
        """The model wrote code without saying which file. Offer to save it."""
        code = [b for b in orphans if b.lang in ("", "python", "py", "python3") and len(b.body.strip()) > 40]
        if not code:
            return None
        block = max(code, key=lambda b: len(b.body))
        guess = self.guess_target(prompt) or ("main.py" if not self.project.exists("main.py") else None)
        if self.print_mode or not ui.is_interactive():
            path = guess if self.auto else None
        else:
            hint = f" [{guess}]" if guess else ""
            ans = console.input(f"[bold]Save the generated code to a file?[/] path{hint} "
                                f"[muted](enter = accept, n = skip)[/] [bc]❯[/] ").strip()
            path = None if ans.lower() in ("n", "no") else (ans or guess)
        path = valid_path(path) if path else None
        if not path:
            return None
        old = self.project.read(path) if self.project.exists(path) else None
        return Edit(path, new=block.body.rstrip("\n") + "\n", old=old)

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
                c = ui.choose(f"Do you want to {verb} {e.path}?",
                              ["Yes", "Yes, and don't ask again this session (shift+tab)",
                               "No, and tell bcagent what to do differently"])
                if c == 1:
                    self.auto = True
                elif c == 2:
                    fb = console.input("[bc]❯[/] [muted]what should bcagent do instead?[/] ").strip()
                    feedback = fb or None
                    break
            write_edit(self.project, e)
            batch.append((e.path, e.old))
            applied.append(e.path)
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

    # -- commands -------------------------------------------------------------
    def offer_commands(self, commands: List[str]) -> None:
        if not commands or self.print_mode or not ui.is_interactive():
            for c in commands:
                console.print(f"  [muted]suggested command:[/] {c}")
            return
        for cmd in commands[:5]:
            c = ui.choose(f"Run this command?  [bc]{cmd}[/]", ["Yes", "No"])
            if c != 0:
                continue
            code, out = run_command(cmd, str(self.project.root))
            if code != 0:
                console.print(f"  [err]⎿  exit code {code}[/]")
                if ui.choose("Ask bcagent to fix this error?", ["Yes", "No"]) == 0:
                    return self.turn(f"I ran `{cmd}` and it failed with exit code {code}:\n```\n"
                                     f"{out[-3000:]}\n```\nFix the code.")
            else:
                console.print("  [ok]⎿  done[/]")


def _fix_command(cmd: str) -> str:
    """Map python/pip to whatever exists on this machine."""
    head, _, rest = cmd.partition(" ")
    if head in ("python", "python3") and not shutil.which(head):
        alt = shutil.which("python3") or shutil.which("python") or shutil.which("py") or sys.executable
        return f'"{alt}" {rest}'.strip()
    if head in ("pip", "pip3") and not shutil.which(head):
        alt = shutil.which("python3") or shutil.which("python") or sys.executable
        return f'"{alt}" -m pip {rest}'.strip()
    return cmd


def run_command(cmd: str, cwd: str) -> Tuple[int, str]:
    cmd = _fix_command(cmd)
    console.print(f"[bc]$[/] {cmd}", highlight=False)
    env = dict(os.environ, PYTHONUNBUFFERED="1")
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
