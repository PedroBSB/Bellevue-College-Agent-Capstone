from bcagent.imports import add_missing_imports, missing_imports


def test_finds_forgotten_imports():
    src = '"""Doc."""\nimport os\n\ndef load(p):\n    data = json.load(open(p))\n    df = pd.DataFrame(data)\n' \
          '    return Path(p), datetime.date.today(), df\n'
    assert missing_imports(src) == ["from pathlib import Path", "import datetime", "import json",
                                    "import pandas as pd"]
    new, added = add_missing_imports(src)
    assert new.startswith('"""Doc."""\nimport os\nfrom pathlib import Path\nimport datetime') and len(added) == 4
    compile(new, "x", "exec")


def test_leaves_defined_names_alone():
    src = "def f(json, pd):\n    time = 3\n    return json, pd, time\n\nfor np in range(3):\n    print(np)\n"
    assert missing_imports(src) == []
    assert add_missing_imports("x = 1\n") == ("x = 1\n", [])


def test_session_adds_import(make_session):
    s = make_session("FILE: cfg.py\n```python\ndef load(p):\n    return json.load(open(p))\n```\n")
    s.turn("create cfg.py with load(p) that reads json")
    text = (s.project.root / "cfg.py").read_text(encoding="utf-8")
    assert text.startswith("import json\n")


def test_datetime_class_style():
    assert missing_imports("d = datetime.strptime('2024', '%Y')\n") == ["from datetime import datetime"]
    assert missing_imports("d = datetime.date(2024, 1, 1) + timedelta(days=1)\n") == [
        "from datetime import timedelta", "import datetime"]
