from bcagent.sections import find_defs, render, select, splice

SRC = '''import os


class Store:
    def load(self):
        return 1

    def save(self):
        return 2


@decorated
def top(x):
    return x


def other():
    return 3


if __name__ == "__main__":
    top(1)
'''


def test_find_and_select():
    names = [d.name for d in find_defs(SRC)]
    assert names == ["Store", "load", "save", "top", "other"]
    assert [d.name for d in select(SRC, "fix save and top")] == ["save", "top"]
    assert [d.name for d in select(SRC, "rename Store.save")] == ["Store"]   # class contains save


def test_render_hides_the_rest():
    view = render(SRC, select(SRC, "fix top"))
    assert "@decorated\ndef top(x):" in view and "class Store" not in view and "hidden" in view


def test_splice_method_keeps_indentation():
    new, replaced, added, err = splice(SRC, "def save(self):\n    return 20\n")
    assert err is None and replaced == ["save"]
    assert "    def save(self):\n        return 20" in new and "def load" in new


def test_splice_decorated_and_new_function_and_import():
    code = "import json\n\n@decorated\ndef top(x):\n    return json.dumps(x)\n\ndef extra():\n    pass\n"
    new, replaced, added, err = splice(SRC, code)
    assert err is None and replaced == ["top"] and added == ["extra"]
    assert new.index("import json") < new.index("class Store")
    assert new.index("def extra") < new.index('if __name__')
    assert new.count("@decorated") == 1


def test_splice_errors():
    assert splice(SRC, "def top(:\n")[3]
    assert splice(SRC, "x = 1\n")[3]
