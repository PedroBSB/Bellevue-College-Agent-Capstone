"""A start-up suggestion once saved an old copy of the settings and lost the agent it had just installed:
config.json said "agent": "bulldog-1.5b" with "agents": {}, and bcagent stopped at every start."""
import json

import pytest

from bcagent import agents, cli, config, hardware, ui


@pytest.fixture
def cfg_file(tmp_path, monkeypatch):
    f = tmp_path / "config.json"
    monkeypatch.setattr(config, "CONFIG_FILE", f)
    return f


def test_accepting_suggestion_keeps_installed_agent(cfg_file, make_session, monkeypatch):
    s = make_session()
    s.print_mode = False
    monkeypatch.setattr(ui, "is_interactive", lambda: True)
    monkeypatch.setattr(hardware, "total_ram_gb", lambda: 16.0)
    monkeypatch.setattr(ui, "choose", lambda q, o, cancel=None: 0)   # "Yes, use bulldog-1.5b"
    monkeypatch.setattr(cli, "ensure_model", lambda client, model: True)   # no download
    cfg = config.load_config()   # loaded at start-up, before the agent is installed
    hardware.maybe_suggest(s, cfg, None)
    saved = json.loads(cfg_file.read_text())
    assert saved["agent"] == "bulldog-1.5b" and "bulldog-1.5b" in saved["agents"]
    assert saved["recommend_asked"] is True and s.agent.name == "bulldog-1.5b"
    assert cfg == saved


def test_agents_use_keeps_other_settings(cfg_file):
    cfg = config.load_config()
    agents.install("bulldog-1.5b")   # saved after cfg was loaded
    assert cli.agents_command(["use", "bulldog-1.5b"], None, cfg) == 0
    saved = json.loads(cfg_file.read_text())
    assert saved["agent"] == "bulldog-1.5b" and "bulldog-1.5b" in saved["agents"]


def _start(monkeypatch, *argv):
    used = []
    monkeypatch.setattr(cli, "ensure_model", lambda client, model: used.append(model) and False)
    with pytest.raises(SystemExit):
        cli.main(["-p", "hi", *argv])
    return used


def test_startup_reinstalls_missing_catalog_agent(cfg_file, monkeypatch):
    cfg_file.write_text(json.dumps({"agent": "bulldog-1.5b", "agents": {}}))
    assert _start(monkeypatch)[-1] == "qwen2.5-coder:1.5b"
    assert "bulldog-1.5b" in json.loads(cfg_file.read_text())["agents"]


def test_startup_falls_back_to_bulldog(cfg_file, monkeypatch):
    cfg_file.write_text(json.dumps({"agent": "gone-agent", "agents": {}}))
    assert _start(monkeypatch)[-1] == config.DEFAULT_MODEL
    assert json.loads(cfg_file.read_text())["agent"] == "bulldog"


def test_startup_unknown_explicit_agent_still_fails(cfg_file, monkeypatch):
    with pytest.raises(SystemExit) as e:
        cli.main(["-p", "hi", "-a", "gone-agent"])
    assert e.value.code == 1


def test_installer_repair(cfg_file):
    cfg_file.write_text(json.dumps({"agent": "bulldog-1.5b", "agents": {}, "num_ctx": 4096}))
    assert agents.repair_config() is True
    saved = json.loads(cfg_file.read_text())
    assert saved["agent"] == "bulldog" and saved["num_ctx"] == 4096
    assert agents.repair_config() is False
