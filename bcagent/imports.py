"""Add imports small models forget.

Small models often write `json.dumps(...)` or `pd.read_csv(...)` without the import line, so the
code crashes with NameError. After every edit bcagent checks the file: a name that is used but
never defined, and that is a well-known module (or its usual alias, like `pd` for pandas), gets its
import added at the top. Nothing else is changed.
"""
from __future__ import annotations

import ast
import builtins
import re
import sys
from typing import List, Optional, Tuple

ALIASES = {
    "pd": "import pandas as pd", "np": "import numpy as np", "plt": "import matplotlib.pyplot as plt",
    "sm": "import statsmodels.api as sm", "smf": "import statsmodels.formula.api as smf",
    "sf": "import statsformula as sf", "sns": "import seaborn as sns",
}
COMMON = {"json", "os", "sys", "re", "math", "random", "datetime", "time", "csv", "collections", "itertools",
          "functools", "pathlib", "string", "statistics", "typing", "dataclasses", "subprocess", "shutil",
          "glob", "copy", "decimal", "fractions", "heapq", "bisect", "operator", "textwrap", "unittest",
          "argparse", "logging", "pickle", "sqlite3", "hashlib", "uuid", "calendar", "pprint", "io", "tempfile",
          "pandas", "numpy", "sklearn", "statsmodels", "statsformula", "pytest"}
STDLIB = set(getattr(sys, "stdlib_module_names", ())) | COMMON
FROM_IMPORTS = {"Path": "from pathlib import Path", "dataclass": "from dataclasses import dataclass",
                "defaultdict": "from collections import defaultdict", "Counter": "from collections import Counter",
                "deque": "from collections import deque", "namedtuple": "from collections import namedtuple",
                "timedelta": "from datetime import timedelta", "date": "from datetime import date",
                "List": "from typing import List", "Dict": "from typing import Dict",
                "Optional": "from typing import Optional", "Tuple": "from typing import Tuple"}


DATETIME_CLASS_ATTRS = {"strptime", "now", "today", "fromisoformat", "fromtimestamp", "utcnow", "combine",
                        "strftime", "min", "max"}


def _class_style_datetime(tree) -> bool:
    return any(isinstance(n, ast.Attribute) and isinstance(n.value, ast.Name) and n.value.id == "datetime"
               and n.attr in DATETIME_CLASS_ATTRS for n in ast.walk(tree))


def missing_imports(source: str) -> List[str]:
    """Import lines for names that the code uses but never defines."""
    try:
        tree = ast.parse(source)
    except (SyntaxError, ValueError):
        return []
    bound, used = set(dir(builtins)), {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Name):
            if isinstance(node.ctx, (ast.Store, ast.Del)):
                bound.add(node.id)
            else:
                used.setdefault(node.id, node)
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            bound.add(node.name)
        elif isinstance(node, ast.arg):
            bound.add(node.arg)
        elif isinstance(node, (ast.Import, ast.ImportFrom)):
            for a in node.names:
                bound.add((a.asname or a.name).split(".")[0])
        elif isinstance(node, ast.ExceptHandler) and node.name:
            bound.add(node.name)
        elif isinstance(node, (ast.Global, ast.Nonlocal)):
            bound.update(node.names)
    out = []
    for name in used:
        if name in bound:
            continue
        if name in ALIASES:
            out.append(ALIASES[name])
        elif name in FROM_IMPORTS:
            out.append(FROM_IMPORTS[name])
        elif name == "datetime" and _class_style_datetime(tree):
            out.append("from datetime import datetime")   # datetime.strptime(...) means the class
        elif name in STDLIB and not name.startswith("_"):
            out.append(f"import {name}")
    return sorted(set(out))


def add_missing_imports(source: str) -> Tuple[str, List[str]]:
    """(new source, import lines added)."""
    lines_to_add = missing_imports(source)
    if not lines_to_add:
        return source, []
    lines = source.splitlines()
    at = 0
    try:
        tree = ast.parse(source)
        body = tree.body
        if body and isinstance(body[0], ast.Expr) and isinstance(getattr(body[0], "value", None), ast.Constant) \
                and isinstance(body[0].value.value, str):
            at = body[0].end_lineno or 0          # after the module docstring
        for node in body:
            if isinstance(node, (ast.Import, ast.ImportFrom)):
                at = max(at, node.end_lineno or node.lineno)
            elif not (isinstance(node, ast.Expr) and node is body[0]):
                break
    except SyntaxError:
        pass
    while at < len(lines) and re.match(r"#!|# -\*-", lines[at] if at < len(lines) else ""):
        at += 1
    new = lines[:at] + lines_to_add + ([""] if at == 0 and lines else []) + lines[at:]
    return "\n".join(new) + ("\n" if source.endswith("\n") else ""), lines_to_add
