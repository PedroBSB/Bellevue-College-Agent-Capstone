"""Default Python libraries in the environment where bcagent runs your code.

Every bcagent install has numpy, pandas, scikit-learn, statsmodels and statsformula available in
its run environment (~/.bcagent/runenv), so data-analysis code the agent writes runs right away.
bcagent checks at start-up (a fast check, no network) and installs whatever is missing. The
installers already do this at install time.

Change the list with "default_packages" in ~/.bcagent/config.json (pip names), or set
BCAGENT_NO_PACKAGES=1 to skip the check.
"""
from __future__ import annotations

import os
import subprocess
from typing import List, Optional, Tuple

from rich.markup import escape

from . import ui
from .ui import console

# pip name -> (import name, minimum Python minor version)
DEFAULT = ["numpy", "pandas", "scikit-learn", "statsmodels", "statsformula"]
IMPORT_NAME = {"scikit-learn": "sklearn", "pyyaml": "yaml", "beautifulsoup4": "bs4", "pillow": "PIL",
               "opencv-python": "cv2", "python-dateutil": "dateutil"}
NEEDS_PY = {"statsformula": (3, 10)}
PIP_NAME = {v: k for k, v in IMPORT_NAME.items()} | {"sklearn": "scikit-learn"}

PROMPT_NOTE = """Installed Python libraries (use them for data analysis, statistics and machine learning; use the
standard library for everything else): {libs}.
- statsformula gives R-style formulas: `import statsformula as sf; y, X = sf.dmatrices("y ~ x1 + C(group)", df)`
  and `from statsformula.sklearn import FormulaEstimator` to use a formula with a scikit-learn model.
- statsmodels: `import statsmodels.formula.api as smf; smf.ols("y ~ x", data=df).fit().summary()`."""


def wanted(cfg: Optional[dict] = None) -> List[str]:
    if os.environ.get("BCAGENT_NO_PACKAGES"):
        return []
    if cfg is None:
        from .config import load_config
        cfg = load_config()
    pkgs = cfg.get("default_packages")
    return list(DEFAULT if pkgs is None else pkgs)


def import_name(pip_name: str) -> str:
    base = pip_name.split("[")[0].split("=")[0].split(">")[0].split("<")[0].strip()
    return IMPORT_NAME.get(base.lower(), base.replace("-", "_"))


def python_version(py: str) -> Tuple[int, int]:
    try:
        out = subprocess.run([py, "-c", "import sys; print(sys.version_info[0], sys.version_info[1])"],
                             capture_output=True, text=True, timeout=30).stdout.split()
        return int(out[0]), int(out[1])
    except (OSError, subprocess.SubprocessError, ValueError, IndexError):
        return (0, 0)


def missing(py: str, pkgs: List[str]) -> List[str]:
    """The packages that can't be imported by `py` (checked without importing them – fast)."""
    if not pkgs:
        return []
    names = [import_name(p) for p in pkgs]
    code = ("import importlib.util as u, sys\n"
            f"names = {names!r}\n"
            "print('\\n'.join(n for n in names if u.find_spec(n) is None))")
    try:
        r = subprocess.run([py, "-c", code], capture_output=True, text=True, timeout=60)
    except (OSError, subprocess.SubprocessError):
        return list(pkgs)
    gone = set(r.stdout.split())
    return [p for p, n in zip(pkgs, names) if n in gone]


def pip_install(py: str, pkgs: List[str], quiet: bool = False) -> bool:
    cmd = [py, "-m", "pip", "install", "--disable-pip-version-check", "--quiet", *pkgs]
    try:
        if quiet:
            r = subprocess.run(cmd, capture_output=True, text=True, timeout=1800)
        else:
            with console.status(f"[bc]Installing {', '.join(pkgs)}… (one time, may take a few minutes)[/]",
                                spinner="dots", spinner_style=ui.BC_BLUE_LIGHT):
                r = subprocess.run(cmd, capture_output=True, text=True, timeout=1800)
    except (OSError, subprocess.SubprocessError) as e:
        console.print(f"  [warn]⚠ could not install {', '.join(pkgs)}: {escape(str(e))}[/]")
        return False
    if r.returncode != 0:
        tail = "\n".join((r.stdout + r.stderr).strip().splitlines()[-4:])
        console.print(f"  [warn]⚠ could not install {', '.join(pkgs)}[/] [muted](offline? bcagent tries again "
                      f"next time)[/]\n[muted]{escape(tail)}[/]")
        return False
    return True


def ensure(cfg: Optional[dict] = None, py: Optional[str] = None, quiet: bool = False) -> List[str]:
    """Make sure the default libraries are installed in bcagent's run environment.
    Returns the packages that are still missing."""
    pkgs = wanted(cfg)
    if not pkgs:
        return []
    if py is None:
        from .session import run_env
        exe = run_env()
        if exe is None:
            return pkgs
        py = str(exe)
    todo = missing(py, pkgs)
    if not todo:
        return []
    ver = python_version(py)
    skipped = [p for p in todo if ver < NEEDS_PY.get(p, (0, 0))]
    todo = [p for p in todo if p not in skipped]
    for p in skipped:
        need = ".".join(map(str, NEEDS_PY[p]))
        console.print(f"  [warn]⚠ {p} needs Python {need}+ but bcagent runs on Python {ver[0]}.{ver[1]}.[/] "
                      "[muted]Re-run the bcagent installer to get a newer Python.[/]")
    if todo:
        if not quiet:
            console.print(f"[bc]●[/] Setting up the default Python libraries: [bold]{', '.join(todo)}[/]")
        if pip_install(py, todo, quiet=quiet):
            todo = missing(py, todo)
            if not todo and not quiet:
                console.print("  [ok]⎿[/]  ready – your code can use them right away")
    return todo + skipped


def status(cfg: Optional[dict] = None) -> str:
    """One line for `bcagent doctor`."""
    pkgs = wanted(cfg)
    if not pkgs:
        return "[muted]default libraries turned off[/]"
    from .session import _venv_python
    from .config import HOME
    py = _venv_python(HOME / "runenv")
    if not py:
        return "[muted]run environment not created yet (it is set up on first start)[/]"
    gone = missing(str(py), pkgs)
    have = [p for p in pkgs if p not in gone]
    line = "[ok]✓[/] " + ", ".join(have) if have else ""
    if gone:
        line += ("  " if line else "") + "[err]✗ missing:[/] " + ", ".join(gone) + " – run [bc]bcagent packages[/]"
    return line


def command(argv: List[str]) -> int:
    """`bcagent packages [install|list]`."""
    sub = argv[0] if argv else "install"
    if sub == "list":
        console.print(status())
        return 0
    left = ensure(quiet=False)
    if not left:
        console.print("[ok]✓[/] default libraries installed: " + ", ".join(wanted()))
        return 0
    return 1


def pip_name_for(module: str) -> str:
    top = module.split(".")[0]
    return PIP_NAME.get(top, top)
