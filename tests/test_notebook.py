import json

from conftest import sample_nb

from bcagent import colab


def test_roundtrip_keeps_everything():
    nb = sample_nb()
    text = colab.notebook_to_text(nb, "Demo.ipynb")
    assert "# %% [markdown]" in text and "!pip install pandas" in text
    assert colab.merge_cells(nb, colab.text_to_cells(text))["cells"] == nb["cells"]
    assert colab.check_source(text) is None


def test_changed_cell_loses_outputs_new_cell_gets_id():
    nb = sample_nb()
    text = colab.notebook_to_text(nb, "x").replace("x = 1", "x = 2") + "\n# %%\nprint('new')\n"
    out = colab.merge_cells(nb, colab.text_to_cells(text))["cells"]
    assert out[1]["outputs"], "unchanged cell keeps outputs"
    assert out[2]["outputs"] == [] and out[2]["metadata"]["id"] == "c2"
    assert out[3]["source"] == ["print('new')"] and out[3]["metadata"]["id"]


def test_syntax_error_detected():
    assert colab.check_source("# %%\ndef f(:\n")


def test_parse_url():
    good = "1AbCdEfGhIjKlMnOpQrStUvWxYz012345"
    for u in (f"https://colab.research.google.com/drive/{good}?usp=sharing#scrollTo=x",
              f"https://drive.google.com/file/d/{good}/view", good):
        assert colab.parse_url(u) == good
    for bad in ("https://colab.research.google.com/github/a/b/blob/main/x.ipynb", "hello"):
        try:
            colab.parse_url(bad)
        except colab.ColabError:
            continue
        raise AssertionError(bad)


def test_local_ipynb_edit_and_undo(make_session):
    s = make_session("Plotting cell.\n\nFILE: lab.ipynb\n```python\n# %% [markdown]\n# # Title\n#\n# Some *text*\n\n"
                     "# %%\n!pip install pandas\nimport pandas as pd\n\n# %%\nx = 1\nprint(x)\n\n# %%\nprint('plot')\n```\n")
    path = s.project.root / "lab.ipynb"
    raw = json.dumps(sample_nb(), indent=1)
    path.write_text(raw)
    view = s.project.read("lab.ipynb")
    assert view.startswith("# Jupyter notebook:") and "# %%" in view
    s.turn("add a cell to @lab.ipynb that prints plot")
    nb = json.loads(path.read_text(encoding="utf-8"))
    assert len(nb["cells"]) == 4 and nb["cells"][1]["outputs"], "outputs of unchanged cells kept"
    assert nb["cells"][3]["source"] == ["print('plot')"]
    s.undo()
    assert path.read_text(encoding="utf-8") == raw, "undo restores the exact original JSON"


def test_new_ipynb_created_from_cells(make_session):
    s = make_session("FILE: new.ipynb\n```python\n# %%\nprint(1)\n\n# %% [markdown]\n# hi\n```\n")
    s.turn("create new.ipynb")
    nb = json.loads((s.project.root / "new.ipynb").read_text(encoding="utf-8"))
    assert [c["cell_type"] for c in nb["cells"]] == ["code", "markdown"]


def test_code_under_markdown_marker_is_kept_as_code():
    cells = colab.text_to_cells("# %% [markdown]\n# Sum\nprint(sum([1, 2]))\n")
    assert cells == [("code", "# Sum\nprint(sum([1, 2]))")]
