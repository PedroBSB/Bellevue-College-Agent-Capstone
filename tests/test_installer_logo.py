"""The installers show the same Bulldog badge as bcagent itself (bcagent/ui.py bulldog_badge)."""
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
from rich.console import Console
from rich.text import Text

from bcagent import ui

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import installer_logo  # noqa: E402


@pytest.mark.parametrize("name", ["install.sh", "install.ps1"])
def test_installer_logo_data_is_current(name):
    f = ROOT / name
    assert installer_logo.updated(f) == f.read_text(encoding="utf-8"), \
        "the logo changed: run  python scripts/installer_logo.py"


def cells(ansi: str):
    """(character, foreground, background, bold) for every cell of the badge's lines."""
    con = Console(width=200)
    lines = [ln for ln in ansi.splitlines() if ln and ln.lstrip("\x1b[0123456789;m")[:1] in "╭│╰"]
    out = []
    for ln in lines:
        t = Text.from_ansi(ln)
        row = []
        for i, ch in enumerate(t.plain):
            st = t.get_style_at_offset(con, i)
            rgb = lambda c: c and tuple(c.get_truecolor())   # noqa: E731
            row.append((ch, rgb(st.color) if ch != " " else None, rgb(st.bgcolor), bool(st.bold) and ch != " "))
        out.append(row)
    return out


def app_badge(system: str) -> str:
    """The badge as bcagent prints it (in a new process: rich caches each style's codes for one color system)."""
    code = ("import io, sys; from rich.console import Console; from bcagent import ui; b = io.StringIO(); "
            f"Console(file=b, force_terminal=True, color_system={system!r}, width=120).print(ui.bulldog_badge()); "
            "sys.stdout.buffer.write(b.getvalue().encode('utf-8'))")
    return subprocess.run([sys.executable, "-c", code], capture_output=True, check=True, cwd=ROOT).stdout.decode("utf-8")


@pytest.mark.skipif(shutil.which("bash") is None or os.name == "nt", reason="needs bash")
@pytest.mark.parametrize("colorterm,system", [("truecolor", "truecolor"), ("", "256")])
def test_install_sh_banner_matches_app(colorterm, system):
    text = (ROOT / "install.sh").read_text(encoding="utf-8")
    code = re.search(r"^# >>> bulldog logo data.*?^banner\(\) \{.*?^\}$", text, re.S | re.M).group(0)
    env = dict(os.environ, FORCE_COLOR="1", COLORTERM=colorterm, TERM="xterm-256color", LC_ALL="en_US.UTF-8")
    env.pop("NO_COLOR", None)
    out = subprocess.run(["bash", "-c", code + "\nbanner"], capture_output=True, env=env, check=True).stdout
    assert (b"38;2;" in out) == (system == "truecolor")
    shown = cells(out.decode("utf-8"))
    assert len(shown) == ui.LOGO_LINES + 4
    assert shown == cells(app_badge(system))


@pytest.mark.skipif(shutil.which("pwsh") is None, reason="needs PowerShell")
def test_install_ps1_banner_matches_app():
    text = (ROOT / "install.ps1").read_text(encoding="utf-8")
    code = re.search(r"^# >>> bulldog logo data.*?^function Banner \{.*?^\}$", text, re.S | re.M).group(0)
    env = dict(os.environ, FORCE_COLOR="1")
    env.pop("NO_COLOR", None)
    code = ("[Console]::OutputEncoding = [Text.Encoding]::UTF8\n"
            "if ($PSStyle) { $PSStyle.OutputRendering = 'Ansi' }\n" + code + "\nBanner")
    out = subprocess.run(["pwsh", "-NoProfile", "-Command", code], capture_output=True, env=env, check=True).stdout
    assert cells(out.decode("utf-8")) == cells(app_badge("truecolor"))
