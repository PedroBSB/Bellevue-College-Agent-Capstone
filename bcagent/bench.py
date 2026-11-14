"""`bcagent bench`: measure how well local models do typical student tasks.

Each task is a small, realistic request ("fix the off-by-one bug in @sum_to.py", "read data.csv with
pandas and return the mean score") with an automatic check that runs the resulting code. bcagent
runs every task through its normal agent loop (same prompts, edit parsing, safety checks and
repairs) in a fresh temporary folder, so the score shows what a student would actually get.

    bcagent bench                                  # the models of your installed agents
    bcagent bench --models qwen2.5-coder:0.5b,qwen3.5:0.8b
    bcagent bench --tasks fizzbuzz,big_file --repeat 3
    bcagent bench --pull                           # download missing models first

Results are saved in ~/.bcagent/bench/ (JSON). Temperature 0 and a fixed seed keep runs repeatable.
"""
from __future__ import annotations

import contextlib
import io
import json
import shutil
import subprocess
import tempfile
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Callable, Dict, List, Optional

from rich.markup import escape
from rich.table import Table

from . import ui
from .config import HOME
from .ui import console

BENCH_DIR = HOME / "bench"


@dataclass
class Task:
    name: str
    category: str
    prompt: str
    check: str                                    # Python code; the task passes when it exits with 0
    files: Dict[str, str] = field(default_factory=dict)


def _big_file() -> str:
    body = "".join(f"def helper_{i}(x):\n    return x + {i}\n\n\n" for i in range(300))
    return body + "def parse_price(text):\n    return float(text)\n"


def _notebook() -> str:
    return json.dumps({"nbformat": 4, "nbformat_minor": 5, "metadata": {}, "cells": [
        {"cell_type": "markdown", "id": "a", "metadata": {}, "source": ["# Lab 1"]},
        {"cell_type": "code", "id": "b", "metadata": {}, "execution_count": None, "outputs": [],
         "source": ["nums = [3, 1, 2]"]}]}, indent=1)


TASKS: List[Task] = [
    Task("fizzbuzz", "write", "create fizzbuzz.py with a function fizzbuzz(n) that returns a list of strings for the "
         "numbers 1 to n: 'Fizz' for multiples of 3, 'Buzz' for multiples of 5, 'FizzBuzz' for both, otherwise the "
         "number as a string",
         "from fizzbuzz import fizzbuzz\nassert fizzbuzz(5) == ['1','2','Fizz','4','Buzz']\n"
         "assert fizzbuzz(15)[-1] == 'FizzBuzz' and len(fizzbuzz(15)) == 15"),
    Task("temperatures", "write", "create temps.py with two functions: c_to_f(c) converts Celsius to Fahrenheit and "
         "f_to_c(f) converts Fahrenheit to Celsius",
         "from temps import c_to_f, f_to_c\nassert abs(c_to_f(100) - 212) < 1e-6 and abs(f_to_c(32)) < 1e-6\n"
         "assert abs(c_to_f(-40) + 40) < 1e-6"),
    Task("palindrome", "write", "create text_tools.py with a function is_palindrome(s) that returns True when s reads "
         "the same backwards, ignoring upper/lower case and spaces",
         "from text_tools import is_palindrome as p\nassert p('Never odd or even') and p('a') and not p('hello')"),
    Task("vowels", "write", "create vowels.py with a function count_vowels(s) that returns how many vowels (a, e, i, o, "
         "u, upper or lower case) are in the string s",
         "from vowels import count_vowels as c\nassert c('Hello World') == 3 and c('') == 0 and c('AEIOU') == 5"),
    Task("word_counts", "write", "create words.py with a function word_counts(text) that returns a dictionary mapping "
         "each lowercase word to how many times it appears; ignore the punctuation . , ! ?",
         "from words import word_counts as w\nr = w('The cat. The dog! the END?')\n"
         "assert r == {'the': 3, 'cat': 1, 'dog': 1, 'end': 1}, r"),
    Task("primes", "write", "create primes.py with a function primes_up_to(n) that returns the list of all prime "
         "numbers less than or equal to n",
         "from primes import primes_up_to as p\nassert p(20) == [2,3,5,7,11,13,17,19] and p(1) == [] and p(2) == [2]"),
    Task("flatten", "write", "create nested.py with a function flatten(items) that turns a nested list like "
         "[1, [2, [3]], 4] into a flat list [1, 2, 3, 4]",
         "from nested import flatten\nassert flatten([1, [2, [3]], 4]) == [1, 2, 3, 4] and flatten([]) == []"),
    Task("dates", "write", "create dates.py with a function days_between(a, b) that takes two dates as 'YYYY-MM-DD' "
         "strings and returns the number of days between them as a positive integer",
         "from dates import days_between as d\nassert d('2024-01-01', '2024-03-01') == 60 and d('2024-03-01', '2024-01-01') == 60"),
    Task("bank_account", "write", "create bank.py with a class BankAccount that starts with balance 0 (or a given "
         "balance), has deposit(amount) and withdraw(amount) methods and a balance attribute; withdraw must raise "
         "ValueError when there is not enough money",
         "from bank import BankAccount\na = BankAccount()\na.deposit(50)\na.withdraw(20)\nassert a.balance == 30\n"
         "try:\n    a.withdraw(100)\n    raise SystemExit('no ValueError')\nexcept ValueError:\n    pass\n"
         "assert a.balance == 30"),
    Task("json_config", "write", "create config_tools.py with a function load_config(path) that returns the dictionary "
         "stored in the JSON file at path, or an empty dictionary {} if the file does not exist",
         "import json\nfrom config_tools import load_config\njson.dump({'a': 1}, open('c.json', 'w'))\n"
         "assert load_config('c.json') == {'a': 1} and load_config('missing.json') == {}"),
    Task("off_by_one", "fix", "fix the bug in @sum_to.py: sum_to(n) should return 1 + 2 + ... + n",
         "from sum_to import sum_to\nassert sum_to(4) == 10 and sum_to(1) == 1",
         {"sum_to.py": "def sum_to(n):\n    total = 0\n    for i in range(n):\n        total += i\n    return total\n"}),
    Task("empty_average", "fix", "make average in @avg.py return 0 for an empty list instead of crashing",
         "from avg import average\nassert average([]) == 0 and average([1, 2, 3]) == 2",
         {"avg.py": "def average(nums):\n    return sum(nums) / len(nums)\n"}),
    Task("syntax_error", "fix", "fix the syntax error in @broken.py",
         "from broken import greet\nassert greet('Ann') == 'Hi Ann'",
         {"broken.py": "def greet(name)\n    return 'Hi ' + name\n"}),
    Task("add_parameter", "fix", "change greet in @hello.py so it takes an optional second argument greeting that "
         "defaults to 'Hello'",
         "from hello import greet\nassert greet('Ann') == 'Hello, Ann!' and greet('Ann', 'Hi') == 'Hi, Ann!'",
         {"hello.py": "def greet(name):\n    return f'Hello, {name}!'\n"}),
    Task("big_file", "fix", "change parse_price in @prices.py so it also accepts a leading '$' and commas, "
         "for example '$1,200.50' becomes 1200.5",
         "import prices\nassert prices.parse_price('$1,200.50') == 1200.5 and prices.parse_price('3') == 3.0\n"
         "assert prices.helper_299(1) == 300, 'the rest of the file must stay'",
         {"prices.py": _big_file()}),
    Task("pandas_mean", "data", "create report.py with a function mean_score(path) that reads the CSV file at path "
         "with pandas and returns the mean of the score column",
         "from report import mean_score\nassert abs(float(mean_score('data.csv')) - 85.0) < 1e-6",
         {"data.csv": "name,score\nann,90\nbob,80\ncy,85\n"}),
    Task("pandas_groupby", "data", "create groups.py with a function avg_by_group(path) that reads the CSV at path "
         "with pandas and returns a dictionary mapping each group to the average of its value column",
         "from groups import avg_by_group\nr = {k: float(v) for k, v in dict(avg_by_group('sales.csv')).items()}\n"
         "assert r == {'a': 2.0, 'b': 10.0}, r",
         {"sales.csv": "group,value\na,1\na,3\nb,10\n"}),
    Task("regression", "data", "create reg.py with a function slope(path) that reads the CSV at path with pandas, "
         "fits the regression y ~ x with the statsmodels formula api and returns the coefficient of x",
         "from reg import slope\nassert abs(float(slope('xy.csv')) - 2.0) < 0.05",
         {"xy.csv": "x,y\n" + "".join(f"{i},{2 * i + 1 + (0.1 if i % 2 else -0.1)}\n" for i in range(12))}),
    Task("notebook_cell", "data", "add a code cell to @lab.ipynb that sets sorted_nums to the list nums sorted from "
         "smallest to largest",
         "import json\nnb = json.load(open('lab.ipynb'))\nns = {}\nfor c in nb['cells']:\n"
         "    if c['cell_type'] == 'code':\n        exec(''.join(c['source']), ns)\nassert ns['sorted_nums'] == [1, 2, 3]",
         {"lab.ipynb": _notebook()}),
    Task("write_tests", "tests", "write pytest tests for @calc.py in tests/test_calc.py",
         "import shutil, subprocess, sys, pathlib\n"
         "ok = subprocess.run([sys.executable, '-m', 'pytest', '-q', 'tests'], capture_output=True)\n"
         "assert ok.returncode == 0, 'tests must pass on the correct code'\n"
         "src = pathlib.Path('calc.py').read_text()\n"
         "pathlib.Path('calc.py').write_text(src.replace('return a - b', 'return a + b'))\n"
         "bad = subprocess.run([sys.executable, '-m', 'pytest', '-q', 'tests'], capture_output=True)\n"
         "pathlib.Path('calc.py').write_text(src)\n"
         "assert bad.returncode != 0, 'tests must catch a bug in sub'",
         {"calc.py": "def add(a, b):\n    return a + b\n\n\ndef sub(a, b):\n    return a - b\n"}),
]


@dataclass
class Result:
    model: str
    task: str
    category: str
    passed: bool
    seconds: float
    tokens: int
    detail: str = ""


def _python() -> str:
    from .session import python_for
    return python_for(tempfile.gettempdir())


def run_task(client, model: str, task: Task, edit_format: str = "whole", timeout: float = 300) -> Result:
    from .agents import Agent
    from .project import Project
    from .session import Session
    folder = Path(tempfile.mkdtemp(prefix=f"bcagent-bench-{task.name}-"))
    try:
        for name, text in task.files.items():
            (folder / name).parent.mkdir(parents=True, exist_ok=True)
            (folder / name).write_text(text, encoding="utf-8")
        agent = Agent("bench", model, edit_format=edit_format, temperature=0.0)
        cfg = {"num_ctx": 8192, "max_tokens": 2048, "max_repairs": 1, "seed": 7, "default_packages": []}
        session = Session(Project(folder), agent, cfg, client, auto=True, print_mode=True)
        tokens = 0
        orig = client.chat_stream

        def counting(*a, **k):
            nonlocal tokens
            yield from orig(*a, **k)
            tokens += int(client.last_stats.get("eval_count", 0) or 0)
        client.chat_stream = counting
        start = time.time()
        quiet = (ui.console.quiet, ui.err_console.quiet)
        ui.console.quiet = ui.err_console.quiet = True
        try:
            with contextlib.redirect_stdout(io.StringIO()):
                session.turn(task.prompt)
        except Exception as e:  # a crash counts as a failure, the benchmark goes on
            return Result(model, task.name, task.category, False, time.time() - start, tokens,
                          f"bcagent error: {type(e).__name__}: {e}"[:200])
        finally:
            ui.console.quiet, ui.err_console.quiet = quiet
            client.chat_stream = orig
        seconds = time.time() - start
        try:
            import os
            env = dict(os.environ, PYTEST_DISABLE_PLUGIN_AUTOLOAD="1")   # broken global pytest plugins
            r = subprocess.run([_python(), "-c", task.check], cwd=folder, capture_output=True, text=True,
                               timeout=60, env=env)
            passed = r.returncode == 0
            detail = "" if passed else (r.stderr.strip().splitlines() or ["failed"])[-1][:200]
        except subprocess.TimeoutExpired:
            passed, detail = False, "check timed out"
        return Result(model, task.name, task.category, passed, seconds, tokens, detail)
    finally:
        shutil.rmtree(folder, ignore_errors=True)


def run(client, models: List[str], tasks: List[Task], repeat: int = 1,
        on_result: Optional[Callable[[Result], None]] = None) -> List[Result]:
    results = []
    for model in models:
        for _ in range(repeat):
            for task in tasks:
                r = run_task(client, model, task)
                results.append(r)
                if on_result:
                    on_result(r)
    return results


def summarize(results: List[Result]) -> List[dict]:
    rows = []
    for model in dict.fromkeys(r.model for r in results):
        rs = [r for r in results if r.model == model]
        secs = sum(r.seconds for r in rs)
        toks = sum(r.tokens for r in rs)
        cats = {}
        for r in rs:
            c = cats.setdefault(r.category, [0, 0])
            c[0] += r.passed
            c[1] += 1
        rows.append({"model": model, "passed": sum(r.passed for r in rs), "total": len(rs),
                     "avg_seconds": secs / max(1, len(rs)), "tokens_per_second": toks / secs if secs else 0,
                     "categories": {k: f"{v[0]}/{v[1]}" for k, v in cats.items()}})
    return sorted(rows, key=lambda x: (-x["passed"], x["avg_seconds"]))


def print_table(rows: List[dict]) -> None:
    t = Table(border_style=ui.BC_BLUE_LIGHT, header_style="bc")
    for col in ("model", "passed", "write", "fix", "data", "tests", "s/task", "tok/s"):
        t.add_column(col, justify="left" if col == "model" else "right")
    for r in rows:
        c = r["categories"]
        t.add_row(escape(r["model"]), f"{r['passed']}/{r['total']} ({100 * r['passed'] / max(1, r['total']):.0f}%)",
                  c.get("write", "–"), c.get("fix", "–"), c.get("data", "–"), c.get("tests", "–"),
                  f"{r['avg_seconds']:.1f}", f"{r['tokens_per_second']:.0f}")
    console.print(t)


def save(results: List[Result], path: Optional[Path] = None) -> Path:
    from . import __version__
    path = path or BENCH_DIR / f"bench-{datetime.now():%Y%m%d-%H%M%S}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"bcagent": __version__, "date": datetime.now().isoformat(timespec="seconds"),
                                "summary": summarize(results), "results": [asdict(r) for r in results]},
                               indent=1), encoding="utf-8")
    return path


def command(argv: List[str]) -> int:
    import argparse
    from .agents import all_agents
    from .config import load_config
    from .llm import Ollama, OllamaError
    p = argparse.ArgumentParser(prog="bcagent bench", description="Score local models on typical student tasks")
    p.add_argument("--models", help="comma-separated Ollama models (default: the models of your installed agents)")
    p.add_argument("--tasks", help="comma-separated task names (default: all " + str(len(TASKS)) + ")")
    p.add_argument("--repeat", type=int, default=1, help="run every task this many times")
    p.add_argument("--pull", action="store_true", help="download models that are missing")
    p.add_argument("--json", help="where to save the results (default: ~/.bcagent/bench/)")
    p.add_argument("--min-pass", type=int, help="exit with an error if a model passes fewer tasks (for CI)")
    p.add_argument("--list", action="store_true", help="list the tasks and exit")
    a = p.parse_args(argv)
    if a.list:
        for t in TASKS:
            console.print(f"[bc]{t.name:15}[/] [muted]{t.category:6}[/] {escape(t.prompt[:90])}")
        return 0
    cfg = load_config()
    client = Ollama(cfg.get("ollama_host"))
    if not client.ensure_running():
        console.print("[err]✗ Ollama is not running[/]")
        return 1
    models = [m.strip() for m in a.models.split(",")] if a.models else \
        list(dict.fromkeys(ag.model for ag in all_agents(Path.cwd()).values()))
    tasks = TASKS if not a.tasks else [t for t in TASKS if t.name in a.tasks.split(",")]
    try:
        have = set(client.models())
    except OllamaError as e:
        console.print(f"[err]✗ {e}[/]")
        return 1
    missing = [m for m in models if m not in have and f"{m}:latest" not in have]
    if missing and a.pull:
        from .cli import ensure_model
        for m in missing:
            ensure_model(client, m)
    elif missing:
        console.print(f"[warn]skipping models that aren't downloaded:[/] {', '.join(missing)} "
                      "[muted](add --pull to download them)[/]")
        models = [m for m in models if m not in missing]
    if not models or not tasks:
        console.print("[err]✗ nothing to run[/]")
        return 1
    console.print(f"[bc]●[/] {len(tasks)} task(s) × {len(models)} model(s)" +
                  (f" × {a.repeat}" if a.repeat > 1 else "") + " – each runs through bcagent's normal agent loop")

    def show(r: Result) -> None:
        mark = "[ok]✓[/]" if r.passed else "[err]✗[/]"
        console.print(f"  {mark} {escape(r.model):28} {r.task:15} {r.seconds:5.1f}s"
                      + (f"  [muted]{escape(r.detail[:70])}[/]" if r.detail else ""))
    results = run(client, models, tasks, a.repeat, show)
    rows = summarize(results)
    print_table(rows)
    path = save(results, Path(a.json) if a.json else None)
    console.print(f"[muted]Saved to[/] {path}")
    if a.min_pass is not None and any(r["passed"] < a.min_pass for r in rows):
        console.print(f"[err]✗ a model passed fewer than {a.min_pass} tasks[/]")
        return 1
    return 0
