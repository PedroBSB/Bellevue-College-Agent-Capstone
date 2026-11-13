import json

import pytest
from conftest import sample_nb

from bcagent import cli, colab

URL = "https://colab.research.google.com/drive/1AbCdEfGhIjKlMnOpQrStUvWxYz012345"


class FakeDrive:
    def __init__(self, nb):
        self.store = {"nb": nb, "version": "5"}
        self.creds = colab.Credentials()

    def metadata(self, fid):
        return {"id": fid, "name": "My Demo.ipynb", "version": self.store["version"], "mimeType": colab.COLAB_MIME,
                "capabilities": {"canEdit": True}}

    def download(self, fid):
        return json.loads(json.dumps(self.store["nb"]))

    def upload(self, fid, nb, mime):
        self.store["nb"] = nb
        self.store["version"] = str(int(self.store["version"]) + 1)
        return {"version": self.store["version"]}


@pytest.fixture
def drive(monkeypatch):
    monkeypatch.setenv("BCAGENT_GOOGLE_TOKEN", "fake")
    return FakeDrive(sample_nb())


def test_link_edit_push_conflict(tmp_path, drive):
    s = colab.ColabSync(tmp_path)
    s.drive = drive
    rel = s.link(URL)
    assert rel == "colab/my_demo.py"
    p = tmp_path / rel
    p.write_text(p.read_text(encoding="utf-8").replace("x = 1", "x = 3"))
    assert s.push(rel, ask=False)
    assert drive.store["nb"]["cells"][2]["source"] == ["x = 3\n", "print(x)"]
    assert drive.store["nb"]["cells"][1]["outputs"]
    drive.store["version"] = "99"  # changed in Colab meanwhile
    with pytest.raises(colab.ColabError):
        s.push(rel, ask=False)
    assert s.push(rel, ask=False, force=True)
    assert colab.ColabSync(tmp_path).is_mirror(rel), "links persist"


def test_session_edit_uploads_and_undo(make_session, drive):
    s = make_session("FILE: colab/my_demo.py\n```python\n# %%\nprint('only')\n```\n")
    s.colab.drive = drive
    cli.handle_colab(URL, s)
    assert s.project.pinned == ["colab/my_demo.py"]
    s.turn("replace everything with one cell")
    assert [c["source"] for c in drive.store["nb"]["cells"]] == [["print('only')"]]
    s.undo()
    assert len(drive.store["nb"]["cells"]) == 3


def test_not_signed_in_noninteractive(tmp_path):
    s = colab.ColabSync(tmp_path)
    assert s.ensure_signed_in() is False
