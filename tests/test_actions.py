import pytest

from bcagent.actions import route

CASES = [
    ("commit and push", ["/git commit", "/git push"], True),
    ("commit my changes and push them to github", ["/git commit", "/git push"], True),
    ("run the tests", ["/test"], True),
    ("create a github repo for this project", ["/github create"], True),
    ("open a pull request", ["/github pr"], True),
    ("undo the last commit", ["/git revert"], True),
    ("save it to colab", ["/colab push"], True),
    ("set up an ssh key", ["/github ssh"], True),
    ("what changed?", ["/git"], True),
    ("fix the bug in calc.py and commit", ["/git commit"], False),
    ("add a push button to the game", [], False),
    ("write a function that pulls data from an api", [], False),
    ("explain what a git commit is", [], False),
    ("how do I push to github?", [], False),
    ("write a commit message function in utils.py", [], False),
    ("write tests for calc.py", [], False),
]


@pytest.mark.parametrize("text,commands,only", CASES)
def test_route(text, commands, only):
    p = route(text)
    assert p.commands == commands
    assert p.action_only == only


def test_mixed_rest_and_colab_link():
    p = route("fix the bug in calc.py and commit")
    assert p.rest == "fix the bug in calc.py"
    p = route("add a cell that plots https://colab.research.google.com/drive/1AbCdEfGhIjKlMnOpQrStUvWxYz0")
    assert p.commands[0].startswith("/colab https://") and p.rest == "add a cell that plots"
