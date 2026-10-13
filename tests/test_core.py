import hashlib
import io
import json
import os
import struct
import zipfile
from pathlib import Path
from threading import Event

import pytest

from neo_unpack.config import load_settings, save_settings
from neo_unpack.detect import detect
from neo_unpack.engine import SevenZip
from neo_unpack.model import Cancelled, InvalidInput, MissingVolume, PasswordRequired, Settings, UnsafeArchive
from neo_unpack.volumes import make_group
from neo_unpack.workflow import UnpackTask
from neo_unpack.ziprepair import repair_zip_copy
from conftest import zip_bytes


def fake_flags(path, local=True, central=True):
    data = bytearray(path.read_bytes())
    with zipfile.ZipFile(path) as z:
        offset = z.start_dir
        infos = z.infolist()
        for info in infos:
            if local:
                flags = struct.unpack_from("<H", data, info.header_offset + 6)[0]
                struct.pack_into("<H", data, info.header_offset + 6, flags | 1)
            if central:
                flags = struct.unpack_from("<H", data, offset + 8)[0]
                struct.pack_into("<H", data, offset + 8, flags | 1)
            name, extra, comment = struct.unpack_from("<HHH", data, offset + 28)
            offset += 46 + name + extra + comment
    path.write_bytes(data)


@pytest.mark.parametrize("prefix", [b"", b"\xff\xd8pretend picture\xff\xd9", b"X" * (4 * 1024 * 1024 - 2)])
def test_disguised_zip(make_zip, prefix):
    path = make_zip("picture.jpg", prefix=prefix)
    found = detect(path)
    assert found.kind == "zip" and found.offset == len(prefix)


def test_false_signature(tmp_path):
    file = tmp_path / "plain.jpg"
    file.write_bytes(b"fake PK\x03\x04 data 7z\xbc\xaf\x27\x1c noise")
    assert detect(file) is None


@pytest.mark.parametrize("local,central", [(True, True), (True, False), (False, True)])
def test_fake_zip(make_zip, local, central):
    file = make_zip(files={"结果.txt": "hello", "second.bin": b"more data"})
    fake_flags(file, local, central)
    assert repair_zip_copy(file) == 2
    with zipfile.ZipFile(file) as z:
        assert z.testzip() is None
        assert z.read("结果.txt") == b"hello"


@pytest.mark.parametrize("kind,headers", [("zip", False), ("7z", False), ("7z", True)])
def test_passwords_and_preserved_original(make_archive, tmp_path, kind, headers):
    archive = make_archive(kind, "中文 p@ss", headers)
    before = hashlib.sha256(archive.read_bytes()).hexdigest()
    task = UnpackTask([archive], Settings(passwords=["wrong", "中文 p@ss"]))
    result = task.run()
    assert (result.output / "结果.bin").read_bytes() == b"payload" * 100
    assert hashlib.sha256(archive.read_bytes()).hexdigest() == before


def test_fake_workflow_keeps_source(make_zip):
    file = make_zip("photo.jpg", files={"content.bin": b"abc"})
    fake_flags(file)
    before = file.read_bytes()
    result = UnpackTask([file], Settings()).run()
    assert (result.output / "content.bin").read_bytes() == b"abc"
    assert file.read_bytes() == before


def test_genuine_encryption_not_modified(make_archive):
    file = make_archive("zip", "actual-password")
    before = file.read_bytes()
    assert repair_zip_copy(file) == 0
    assert file.read_bytes() == before


def test_retry_reuses_completed_layer(make_archive, make_zip):
    encrypted = make_archive("7z", "correct", headers=True)
    outer = make_zip("outer.zip", {"wrapper/secret.7z": encrypted.read_bytes(), "说明.txt": "keep me"})
    task = UnpackTask([outer], Settings(passwords=["wrong"]))
    with pytest.raises(PasswordRequired):
        task.run()
    assert task.archive_count == 1
    assert task.session.exists()
    manifest = (task.session / "task.json").read_text()
    assert '"passwords"' not in manifest and "wrong" not in manifest
    task.settings.passwords.append("correct")
    result = task.run()
    assert task.archive_count == 2
    assert (result.output / "结果.bin").is_file()
    assert (result.output / "说明.txt").read_text() == "keep me"
    assert not task.session.exists()


@pytest.mark.parametrize("extra,expected_layers", [({}, 2), ({"提示.txt": "read me"}, 2), ({"image.png": b"picture"}, 1), ({"提示.txt": "read me", "image.png": b"picture"}, 1)])
def test_recursion_strategy(make_zip, extra, expected_layers):
    inner = zip_bytes({"final.bin": b"done"})
    outer = make_zip(files={"inner.zip": inner, **extra})
    result = UnpackTask([outer], Settings()).run()
    assert result.layers == expected_layers
    assert (result.output / ("final.bin" if expected_layers == 2 else "inner.zip")).exists()


def test_multiple_archives_and_collision(make_zip):
    outer = make_zip(files={"a.zip": zip_bytes({"same.bin": b"a"}), "b.zip": zip_bytes({"same.bin": b"b"}), "note.txt": "keep"})
    result = UnpackTask([outer], Settings()).run()
    assert sorted(p.read_bytes() for p in result.output.glob("*.bin")) == [b"a", b"b"]
    assert (result.output / "note.txt").exists()


def test_one_layer(make_zip):
    inner = zip_bytes({"final.bin": b"done"})
    result = UnpackTask([make_zip(files={"wrapper/inner.zip": inner})], Settings(depth="one")).run()
    assert result.layers == 1 and (result.output / "inner.zip").is_file()


def test_preserve_content_structure(make_zip):
    outer = make_zip(files={"outer/inner/app.exe": b"exe", "outer/inner/assets/x.bin": b"asset"})
    result = UnpackTask([outer], Settings()).run()
    assert (result.output / "app.exe").exists()
    assert (result.output / "assets/x.bin").exists()


def test_keep_intermediates(make_zip):
    result = UnpackTask([make_zip()], Settings(keep_intermediates=True)).run()
    assert (result.output / "hello.txt").exists()
    assert list((result.output / "_中间产物").rglob("*.zip"))


@pytest.mark.parametrize("mode", ["direct", "folder", "fixed", "fixed_folder"])
def test_output_modes(make_zip, tmp_path, mode):
    source = make_zip(files={"result.bin": b"new"})
    fixed = tmp_path / "fixed"
    fixed.mkdir()
    base = fixed if mode.startswith("fixed") else tmp_path
    (base / "result.bin").write_bytes(b"existing")
    result = UnpackTask([source], Settings(output_mode=mode, fixed_directory=str(fixed))).run()
    assert (base / "result.bin").read_bytes() == b"existing"
    assert any(p.read_bytes() == b"new" for p in result.output.glob("*.bin"))


@pytest.mark.parametrize("kind", ["zip", "7z"])
def test_numeric_volumes(make_archive, kind):
    paths = make_archive(kind, "secret", split=100, files={"payload.bin": os.urandom(1500)})
    group = make_group(list(reversed(paths)))
    assert group.paths[0].name.endswith(".001")
    result = UnpackTask(list(reversed(paths)), Settings(passwords=["secret"])).run()
    assert (result.output / "payload.bin").stat().st_size == 1500


def test_reject_batch_and_missing_volumes(make_zip, make_archive):
    with pytest.raises(InvalidInput):
        make_group([make_zip("a.zip"), make_zip("b.zip")])
    paths = make_archive(split=100, files={"payload.bin": os.urandom(1000)})
    with pytest.raises(MissingVolume):
        make_group(paths[1:])
    with pytest.raises(MissingVolume):
        make_group([paths[0], *paths[2:]])
    # Existing files in the input folder must not be discovered implicitly.
    task = UnpackTask([paths[0]], Settings())
    with pytest.raises((MissingVolume, InvalidInput)):
        task.run()


@pytest.mark.parametrize("name", ["../escape.txt", "/absolute.txt", "C:/escape.txt"])
def test_unsafe_paths(make_zip, name):
    source = make_zip(files={name: b"unsafe"})
    with pytest.raises(UnsafeArchive):
        UnpackTask([source], Settings()).run()


def test_config_roundtrip(tmp_path):
    settings = Settings(passwords=["密码", " space "], keep_intermediates=True)
    save_settings(settings, tmp_path)
    assert load_settings(tmp_path) == settings
    if os.name != "nt":
        assert (tmp_path / "settings.json").stat().st_mode & 0o777 == 0o600


def test_cancel(make_zip):
    cancel = Event()
    cancel.set()
    with pytest.raises(Cancelled):
        UnpackTask([make_zip()], Settings(), cancel=cancel).run()


def test_disguised_txt_beside_other_archive(make_zip):
    outer = make_zip(files={"a.txt": zip_bytes({"first.bin": b"a"}), "b.zip": zip_bytes({"second.bin": b"b"})})
    result = UnpackTask([outer], Settings()).run()
    assert (result.output / "first.bin").is_file()
    assert (result.output / "second.bin").is_file()


def test_zip_based_document_is_final_content(make_zip):
    document = zip_bytes({"[Content_Types].xml": "types", "word/document.xml": "document"})
    source = make_zip(files={"document.docx": document})
    result = UnpackTask([source], Settings()).run()
    assert result.layers == 1
    assert (result.output / "document.docx").read_bytes() == document


def test_mixed_content_stops_before_incomplete_nested_volumes(make_zip):
    source = make_zip(files={"a.part2.rar": b"not first volume", "movie.mp4": b"movie", "note.txt": b"note"})
    result = UnpackTask([source], Settings()).run()
    assert result.layers == 1 and (result.output / "movie.mp4").exists()


def test_empty_zip_is_valid(tmp_path):
    path = tmp_path / "empty.zip"
    path.write_bytes(zip_bytes({}))
    result = UnpackTask([path], Settings()).run()
    assert result.output.is_dir() and not list(result.output.iterdir())
