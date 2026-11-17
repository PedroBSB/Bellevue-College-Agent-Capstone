import os
from pathlib import Path

from bcagent import attachments as am, cli

PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 16


def test_dropped_paths_in_every_terminal_style(tmp_path):
    img = tmp_path / "My Pictures" / "plot.png"
    img.parent.mkdir()
    img.write_bytes(PNG)
    windows = os.name == "nt"
    styles = [f"'{img}'", f'"{img}"', img.as_uri()]
    if not windows:
        styles.append(str(img).replace(" ", "\\ "))   # macOS Terminal escapes spaces
    for text in styles:
        assert [p for _, p in am.dropped_paths(f"look at {text} please", windows=windows)] == [img.resolve()]
    assert am.dropped_paths("fix /not/a/real/file.py and calc.py", windows=windows) == []


def test_windows_paths_keep_backslashes():
    assert am._clean('"C:\\Users\\me\\My Pictures\\a.png"', windows=True) == "C:\\Users\\me\\My Pictures\\a.png"
    assert am._clean("C:\\x\\a.png", windows=True) == "C:\\x\\a.png"
    assert am._clean("file:///C:/x/a.png", windows=True) == "C:/x/a.png"


def test_convert_labels_images_files_and_project_files(tmp_path):
    root = tmp_path / "proj"
    root.mkdir()
    (root / "calc.py").write_text("x = 1\n")
    img, notes = tmp_path / "a.png", tmp_path / "notes.txt"
    img.write_bytes(PNG)
    notes.write_text("hello")
    att = am.Attachments(root)
    out = att.convert(f"{img} {notes} {root / 'calc.py'} what is this")
    assert out == "[Image #1] [File #2: notes.txt] @calc.py what is this"
    assert att.convert(str(img)) == "[Image #1]"            # the same file keeps its number
    assert [a.num for a in att.used(out)] == [1, 2]
    assert am.starts_with_path(f"{img} explain") and not am.starts_with_path("/help")


def test_session_sends_image_to_vision_model(make_session, tmp_path):
    s = make_session("A bar chart.")
    s.client.capabilities = lambda model: ["completion", "vision"]
    img = tmp_path / "chart.png"
    img.write_bytes(PNG)
    cli.run_request(f"{img} describe this", s, None, {})
    msg = s.client.prompts[0][-1]
    assert msg["images"] == [am.encode_image(img)]
    assert "Task: [Image #1] describe this" in msg["content"]
    assert not s.images   # not sent again with the next message


def test_text_model_gets_path_and_file_content(make_session, tmp_path):
    s = make_session("OK.")
    img, data = tmp_path / "chart.png", tmp_path / "data.csv"
    img.write_bytes(PNG)
    data.write_text("a,b\n1,2\n")
    cli.run_request(f"'{img}' '{data}' plot it", s, None, {})
    msg = s.client.prompts[0][-1]
    assert "images" not in msg
    assert str(img) in msg["content"] and "a,b\n1,2" in msg["content"]
    assert Path(s.history[0]["content"]) and "[File #2: data.csv]" in s.history[0]["content"]
