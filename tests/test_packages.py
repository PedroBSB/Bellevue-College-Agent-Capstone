import sys

from bcagent import packages


def test_defaults_and_import_names(monkeypatch):
    monkeypatch.delenv("BCAGENT_NO_PACKAGES", raising=False)
    assert packages.wanted({}) == ["numpy", "pandas", "scikit-learn", "statsmodels", "statsformula"]
    assert packages.wanted({"default_packages": []}) == []
    assert packages.import_name("scikit-learn") == "sklearn"
    assert packages.pip_name_for("sklearn.linear_model") == "scikit-learn"


def test_missing_and_ensure(monkeypatch):
    monkeypatch.delenv("BCAGENT_NO_PACKAGES", raising=False)
    py = sys.executable
    assert packages.missing(py, ["pytest", "definitely-not-a-package-xyz"]) == ["definitely-not-a-package-xyz"]
    installed = []
    monkeypatch.setattr(packages, "pip_install", lambda py, pkgs, quiet=False: installed.append(pkgs) or True)
    monkeypatch.setattr(packages, "missing", lambda py, pkgs: [p for p in pkgs if p == "statsformula"])
    monkeypatch.setattr(packages, "python_version", lambda py: (3, 9))
    left = packages.ensure({"default_packages": ["numpy", "statsformula"]}, py=py)
    assert left == ["statsformula"] and installed == [], "statsformula is skipped on Python 3.9"
    monkeypatch.setattr(packages, "python_version", lambda py: (3, 12))
    packages.ensure({"default_packages": ["numpy", "statsformula"]}, py=py)
    assert installed == [["statsformula"]]


def test_prompt_mentions_libraries(make_session, monkeypatch):
    monkeypatch.delenv("BCAGENT_NO_PACKAGES", raising=False)
    s = make_session("ok")
    s.turn("analyze data.csv")
    system = s.client.prompts[0][0]["content"]
    assert "statsformula" in system and "scikit-learn (import sklearn)" in system


def test_missing_module_offers_install(make_session, monkeypatch):
    s = make_session()
    runs = []

    def fake_run(cmd, cwd, extra=None):
        runs.append(cmd)
        if cmd.startswith("python -m pip"):
            return 0, ""
        return (1, "ModuleNotFoundError: No module named 'sklearn'") if len(runs) == 1 else (0, "ok")
    monkeypatch.setattr("bcagent.session.run_command", fake_run)
    s.execute("python model.py")
    assert runs == ["python model.py", "python -m pip install scikit-learn", "python model.py"]
