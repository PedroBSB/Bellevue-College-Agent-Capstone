import sys

from bcagent import bench


class Client:
    """A perfect 'model': answers every task with a known-good solution."""
    SOLUTIONS = {
        "fizzbuzz": "FILE: fizzbuzz.py\n```python\ndef fizzbuzz(n):\n    out = []\n    for i in range(1, n + 1):\n"
                    "        out.append('FizzBuzz' if i % 15 == 0 else 'Fizz' if i % 3 == 0 else 'Buzz' if i % 5 == 0"
                    " else str(i))\n    return out\n```\n",
        "off_by_one": "FILE: sum_to.py\n```python\ndef sum_to(n):\n    return sum(range(1, n + 1))\n```\n",
        "big_file": "FILE: prices.py\n```python\ndef parse_price(text):\n    return float(text.replace('$', '')"
                    ".replace(',', ''))\n```\n",
        "notebook_cell": "FILE: lab.ipynb\n```python\n# %% [markdown]\n# # Lab 1\n\n# %%\nnums = [3, 1, 2]\n\n"
                         "# %%\nsorted_nums = sorted(nums)\n```\n",
    }

    def __init__(self):
        self.last_stats = {}
        self.task = None

    def chat_stream(self, model, messages, options=None):
        assert options["temperature"] == 0 and options["seed"] == 7
        self.last_stats = {"eval_count": 10}
        user = messages[-1]["content"]
        for name, answer in self.SOLUTIONS.items():
            task = next(t for t in bench.TASKS if t.name == name)
            if task.prompt[:40] in user:
                yield answer
                return
        yield "I don't know."


def test_tasks_are_well_formed():
    names = [t.name for t in bench.TASKS]
    assert len(names) == 20 == len(set(names))
    for t in bench.TASKS:
        compile(t.check, t.name, "exec")


def test_run_scores_and_summarizes(monkeypatch, tmp_path):
    monkeypatch.setattr(bench, "_python", lambda: sys.executable)
    tasks = [t for t in bench.TASKS if t.name in ("fizzbuzz", "off_by_one", "big_file", "notebook_cell", "primes")]
    results = bench.run(Client(), ["perfect"], tasks)
    by = {r.task: r.passed for r in results}
    assert by == {"fizzbuzz": True, "off_by_one": True, "big_file": True, "notebook_cell": True, "primes": False}
    row = bench.summarize(results)[0]
    assert row["passed"] == 4 and row["total"] == 5 and row["categories"]["fix"] == "2/2"
    path = bench.save(results, tmp_path / "r.json")
    assert '"passed": 4' in path.read_text()
