import re
from pathlib import Path

from bcagent import cli, config, helptext


def overview_text():
    return " ".join(cmd + " " + desc for _, rows in helptext.GROUPS for cmd, desc in rows)


def test_every_slash_command_is_in_the_help():
    text = overview_text()
    for name, _ in cli.COMMANDS:
        assert name.split()[0] in text, f"{name} missing from /help"


def test_every_subcommand_is_in_bcagent_help():
    src = Path(cli.__file__).read_text(encoding="utf-8")
    subs = set(re.findall(r'argv\[0\] == "(\w+)"', src)) | {"git", "github"}
    assert {"agents", "mcp", "colab", "course", "logs", "bench", "packages", "doctor", "update", "bug", "help"} <= subs
    for sub in subs:
        assert f"bcagent {sub}" in helptext.EPILOG, f"bcagent {sub} missing from --help"


def test_topics_render_and_settings_are_documented(capsys):
    for topic in list(helptext.TOPICS) + ["", "/git", "instructors", "nonsense"]:
        helptext.show(topic)
    out = capsys.readouterr().out
    assert "No help topic 'nonsense'" in out
    settings = helptext.TOPICS["settings"][1]
    for key in config.DEFAULTS:
        if key != "agents":
            assert key in settings, f"setting {key} missing from /help settings"
