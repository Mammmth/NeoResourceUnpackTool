import pytest

from conftest import zip_bytes
from neo_unpack.model import PasswordRequired, Settings
from neo_unpack.workflow import UnpackTask


@pytest.mark.parametrize("mode", ["direct", "folder", "fixed", "fixed_folder"])
def test_nested_disguised_archive_output_name(make_zip, tmp_path, mode):
    inner_name = "[PC]青的结纳 ver1.03"
    inner = b"picture prefix" + zip_bytes({"wrapper/app.exe": b"app", "wrapper/assets/data.bin": b"asset"})
    source = make_zip("1.mp4", {f"wrapper/{inner_name}.jpg": inner, "说明.txt": "keep"}, prefix=b"video prefix")
    fixed = tmp_path / "fixed"
    result = UnpackTask([source], Settings(output_mode=mode, fixed_directory=str(fixed))).run()

    expected = {"direct": tmp_path, "folder": tmp_path / "1", "fixed": fixed, "fixed_folder": fixed / "wrapper"}
    assert result.output == expected[mode]
    assert result.layers == 2
    assert (result.output / "app.exe").read_bytes() == b"app"
    assert (result.output / "assets/data.bin").read_bytes() == b"asset"
    assert (result.output / "说明.txt").read_text() == "keep"


@pytest.mark.parametrize("stop", ["one", "mixed", "empty", "texts", "document"])
def test_fixed_folder_uses_last_extracted_archive(make_zip, tmp_path, stop):
    content = {"inner.zip": zip_bytes({"result.bin": b"done"})}
    if stop == "mixed":
        content["cover.png"] = b"image"
    elif stop == "empty":
        content = {}
    elif stop == "texts":
        content = {"note.txt": b"note"}
    elif stop == "document":
        content = {"book.docx": zip_bytes({"word/document.xml": "document"})}
    source = make_zip("outer.zip", {"middle.jpg": zip_bytes(content)})
    result = UnpackTask([source], Settings(
        output_mode="fixed_folder", fixed_directory=str(tmp_path / "fixed"),
        depth="one" if stop == "one" else "auto",
    )).run()

    assert result.output.name == ("outer" if stop == "one" else "middle")
    assert result.layers == (1 if stop == "one" else 2)
    assert sorted(p.name for p in result.output.iterdir()) == (["middle.jpg"] if stop == "one" else sorted(content))


def test_fixed_folder_multiple_branches_use_common_archive(make_zip, tmp_path):
    bundle = zip_bytes({
        "a.zip": zip_bytes({"first.bin": b"a"}),
        "b.zip": zip_bytes({"deeper.zip": zip_bytes({"second.bin": b"b"})}),
    })
    source = make_zip("outer.zip", {"collection.zip": bundle})
    result = UnpackTask([source], Settings(output_mode="fixed_folder", fixed_directory=str(tmp_path / "fixed"))).run()

    assert result.output.name == "collection"
    assert result.layers == 4
    assert (result.output / "first.bin").read_bytes() == b"a"
    assert (result.output / "second.bin").read_bytes() == b"b"


def test_fixed_folder_collision_keeps_existing_directory(make_zip, tmp_path):
    fixed = tmp_path / "fixed"
    existing = fixed / "inner"
    existing.mkdir(parents=True)
    (existing / "result.bin").write_bytes(b"existing")
    source = make_zip("outer.zip", {"inner.zip": zip_bytes({"result.bin": b"new"})})
    result = UnpackTask([source], Settings(output_mode="fixed_folder", fixed_directory=str(fixed))).run()

    assert result.output == fixed / "inner (2)"
    assert (existing / "result.bin").read_bytes() == b"existing"
    assert (result.output / "result.bin").read_bytes() == b"new"


def test_fixed_folder_retry_uses_inner_archive_name(make_archive, make_zip, tmp_path):
    encrypted = make_archive("7z", "correct", headers=True)
    source = make_zip("outer.zip", {"secret.7z": encrypted.read_bytes()})
    task = UnpackTask([source], Settings(
        output_mode="fixed_folder", fixed_directory=str(tmp_path / "fixed"), passwords=["wrong"],
    ))
    with pytest.raises(PasswordRequired):
        task.run()
    task.settings.passwords.append("correct")
    result = task.run()

    assert result.output.name == "secret"
    assert result.archives == 2
    assert (result.output / "结果.bin").read_bytes() == b"payload" * 100


def test_fixed_folder_nested_volumes_use_group_name(make_archive, make_zip, tmp_path):
    volumes = make_archive("7z", split=100, name="inner", files={"result.bin": b"payload" * 100})
    source = make_zip("outer.zip", {p.name: p.read_bytes() for p in volumes})
    result = UnpackTask([source], Settings(output_mode="fixed_folder", fixed_directory=str(tmp_path / "fixed"))).run()

    assert result.output.name == "inner"
    assert result.layers == 2
    assert (result.output / "result.bin").read_bytes() == b"payload" * 100


@pytest.mark.parametrize("prefix,expected", [("", "archive"), ("文件夹 A/", "文件夹 A"),
                                             ("外包装/文件夹 A v1.03/", "文件夹 A v1.03")],
                         ids=["no-folder", "content-folder", "nested-folders"])
def test_fixed_folder_prefers_content_folder(make_zip, tmp_path, prefix, expected):
    source = make_zip("archive.zip", {
        prefix + "app.exe": b"app", prefix + "assets/data.bin": b"data",
    })
    result = UnpackTask([source], Settings(
        output_mode="fixed_folder", fixed_directory=str(tmp_path / "fixed"),
    )).run()
    assert result.output.name == expected
    assert sorted(p.name for p in result.output.iterdir()) == ["app.exe", "assets"]
    assert (result.output / "assets/data.bin").read_bytes() == b"data"


@pytest.mark.parametrize("stop", ["one", "mixed", "empty", "texts", "document"])
def test_content_folder_name_at_each_stop_rule(make_zip, tmp_path, stop):
    content = {"inner.zip": zip_bytes({"result.bin": b"done"})}
    if stop == "mixed":
        content["cover.png"] = b"image"
    elif stop == "empty":
        content = {}
    elif stop == "texts":
        content = {"note.txt": b"note", "other.txt": b"other"}
    elif stop == "document":
        content = {"book.docx": zip_bytes({"word/document.xml": "document"})}
    files = {f"文件夹 A/{name}": data for name, data in content.items()} if content else {"文件夹 A/": b""}
    files.update({"说明.txt": b"keep", "__MACOSX/ignored": b"ignore", ".DS_Store": b"ignore"})
    source = make_zip("archive.zip", files)
    result = UnpackTask([source], Settings(
        output_mode="fixed_folder", fixed_directory=str(tmp_path / "fixed"),
        depth="one" if stop == "one" else "auto",
    )).run()
    assert result.output.name == "文件夹 A"
    assert sorted(p.name for p in result.output.iterdir()) == sorted([*content, "说明.txt"])
    assert result.layers == 1


def test_outer_wrapper_is_not_reused_as_inner_content_name(make_zip, tmp_path):
    source = make_zip("outer.zip", {"外包装/inner.zip": zip_bytes({"result.bin": b"done"})})
    result = UnpackTask([source], Settings(
        output_mode="fixed_folder", fixed_directory=str(tmp_path / "fixed"),
    )).run()
    assert result.output.name == "inner"
    assert (result.output / "result.bin").read_bytes() == b"done"


def test_multiple_content_folders_fall_back_to_archive_name(make_zip, tmp_path):
    source = make_zip("archive.zip", {"A/a.bin": b"a", "B/b.bin": b"b"})
    result = UnpackTask([source], Settings(
        output_mode="fixed_folder", fixed_directory=str(tmp_path / "fixed"),
    )).run()
    assert result.output.name == "archive"
    assert (result.output / "A/a.bin").read_bytes() == b"a"
    assert (result.output / "B/b.bin").read_bytes() == b"b"


def test_branches_use_common_wrapper_and_avoid_existing_folder(make_zip, tmp_path):
    fixed = tmp_path / "fixed"
    existing = fixed / "合集"
    existing.mkdir(parents=True)
    (existing / "original.bin").write_bytes(b"original")
    source = make_zip("archive.zip", {
        "合集/a.zip": zip_bytes({"A/a.bin": b"a"}),
        "合集/b.zip": zip_bytes({"B/b.bin": b"b"}),
    })
    result = UnpackTask([source], Settings(output_mode="fixed_folder", fixed_directory=str(fixed))).run()
    assert result.output == fixed / "合集 (2)"
    assert (result.output / "a.bin").read_bytes() == b"a"
    assert (result.output / "b.bin").read_bytes() == b"b"
    assert (existing / "original.bin").read_bytes() == b"original"


def test_retry_uses_inner_content_folder(make_archive, make_zip, tmp_path):
    encrypted = make_archive("7z", "correct", headers=True, files={"文件夹 A/app.exe": b"app"})
    source = make_zip("outer.zip", {"外包装/inner.7z": encrypted.read_bytes()})
    task = UnpackTask([source], Settings(
        output_mode="fixed_folder", fixed_directory=str(tmp_path / "fixed"), passwords=["wrong"],
    ))
    with pytest.raises(PasswordRequired):
        task.run()
    task.settings.passwords.append("correct")
    result = task.run()
    assert result.output.name == "文件夹 A"
    assert result.archives == 2
    assert (result.output / "app.exe").read_bytes() == b"app"
