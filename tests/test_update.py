import json

import pytest

from bcagent import config, update


class Resp:
    def __init__(self, url="", body=b""):
        self._url, self._body = url, body

    def geturl(self):
        return self._url

    def read(self, *a):
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def test_latest_release_from_redirect(monkeypatch):
    monkeypatch.setattr(update.urllib.request, "urlopen",
                        lambda req, timeout=0: Resp(f"https://github.com/{update.REPO}/releases/tag/v9.1.0"))
    assert update.fetch_latest() == "9.1.0"


def test_no_release_yet(monkeypatch):
    monkeypatch.setattr(update.urllib.request, "urlopen",
                        lambda req, timeout=0: Resp(f"https://github.com/{update.REPO}/releases"))
    assert update.fetch_latest() is None


def test_sources():
    assert update.source_for("1.4.0").endswith("/archive/refs/tags/v1.4.0.zip")
    assert update.newer("1.10.0", "1.9.9") and not update.newer("1.4.0", "1.4.0")


def test_rollback_pins_and_plain_update_unpins(monkeypatch):
    installed = []
    monkeypatch.setattr(update, "install", lambda src=None: installed.append(src) or True)
    assert update.update_command(["--to", "1.4.0"]) == update.UPDATED
    assert installed[-1].endswith("v1.4.0.zip")
    assert config.load_config()["pinned_version"] == "1.4.0"
    # pinned -> no automatic update
    monkeypatch.delenv("BCAGENT_NO_UPDATE", raising=False)
    monkeypatch.setattr(update, "latest_version", lambda force=False: "99.0.0")
    update.maybe_update(config.load_config(), [], interactive=True)
    assert len(installed) == 1
    # plain update -> unpins and goes to the latest release
    monkeypatch.setattr(update, "is_managed", lambda: True)
    assert update.update_command([]) == update.UPDATED
    assert "pinned_version" not in config.load_config() and installed[-1].endswith("v99.0.0.zip")


def test_list_releases(monkeypatch):
    body = json.dumps([{"tag_name": "v1.6.0"}, {"tag_name": "v1.4.0"}, {"tag_name": "v2.0.0-rc", "prerelease": True}])
    monkeypatch.setattr(update.urllib.request, "urlopen", lambda req, timeout=0: Resp(body=body.encode()))
    monkeypatch.setattr(update.json, "load", lambda r: json.loads(body))
    assert update.list_releases() == ["1.6.0", "1.4.0"]
