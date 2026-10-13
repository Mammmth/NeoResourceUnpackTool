import hashlib
import json
import struct
import zipfile
import zlib
from pathlib import Path
from threading import Event

import pytest

from neo_unpack.config import load_settings, save_settings
from neo_unpack.engine import SevenZip
from neo_unpack.filename_encoding import normalize_zip_names, zip_member_name
from neo_unpack.model import Cancelled, InvalidInput, PasswordRequired, Settings, UnpackError, UnsafeArchive
from neo_unpack.workflow import UnpackTask


@pytest.mark.parametrize("codec,name", [
    ("cp932", "ソフトウェアの説明.txt"), ("gbk", "简体中文说明.txt"),
    ("cp950", "繁體中文說明.txt"), ("cp949", "한국어 설명.txt"),
    ("cp1252", "Français été.txt"), ("cp1250", "Příliš žluťoučký.txt"),
    ("cp1251", "Русский текст.txt"), ("cp1254", "Türkçe açıklama.txt"),
    ("cp1253", "Ελληνικά.txt"), ("cp1256", "العربية.txt"),
    ("cp1255", "עברית.txt"), ("cp1257", "Latviešu.txt"),
    ("cp874", "ภาษาไทย.txt"), ("utf-8", "日本語和中文.txt"),
], ids=lambda value: value if value.isascii() else "filename")
def test_manual_codepages_extract_real_names(make_legacy_zip, codec, name):
    archive = make_legacy_zip(codec, {name: b"unchanged payload"})
    before = hashlib.sha256(archive.read_bytes()).digest()
    result = UnpackTask([archive], Settings(filename_encoding=codec)).run()
    assert (result.output / name).read_bytes() == b"unchanged payload"
    assert hashlib.sha256(archive.read_bytes()).digest() == before


def test_auto_detects_japanese_per_layer(make_legacy_zip, make_zip):
    inner = make_legacy_zip()
    outer = make_zip("outer.zip", {"nested.zip": inner.read_bytes()})
    logs = []
    result = UnpackTask([outer], Settings(filename_encoding="auto"), log=logs.append).run()
    assert result.layers == 2
    assert (result.output / "日本語のテスト.txt").read_bytes() == b"payload"
    assert any("自动推测" in line for line in logs)


def test_auto_ambiguous_keeps_default(make_legacy_zip, tmp_path):
    archive = make_legacy_zip("cp1252", {"é.txt": b"content"})
    target = tmp_path / "converted.zip"
    logs = []
    assert not normalize_zip_names(archive, target, "auto", Event(), logs.append)
    assert not target.exists()
    assert any("不确定" in line for line in logs)


@pytest.mark.parametrize("encoding", ["cp932", "auto", "utf-8"])
def test_unicode_names_take_precedence(make_zip, encoding):
    archive = make_zip(files={"中文と日本語.txt": b"content"})
    result = UnpackTask([archive], Settings(filename_encoding=encoding)).run()
    assert (result.output / "中文と日本語.txt").read_bytes() == b"content"


def test_invalid_manual_encoding_is_actionable(make_legacy_zip):
    archive = make_legacy_zip()
    task = UnpackTask([archive], Settings(filename_encoding="utf-8"))
    with pytest.raises(UnpackError, match="更换文件名编码"):
        task.run()
    assert task.session.exists() and archive.exists()


@pytest.mark.parametrize("zip64", [False, True])
def test_normalization_preserves_local_data_and_comments(make_legacy_zip, tmp_path, zip64):
    archive = make_legacy_zip(zip64=zip64)
    target = tmp_path / "converted.zip"
    with zipfile.ZipFile(archive) as original:
        start = original.start_dir
    assert normalize_zip_names(archive, target, "cp932", Event())
    assert target.read_bytes()[:start] == archive.read_bytes()[:start]
    with zipfile.ZipFile(target) as result:
        assert result.testzip() is None
        assert result.comment == b"keep archive comment"
        assert result.infolist()[0].comment == b"keep entry comment"
        assert zip_member_name(result.infolist()[0]) == "日本語のテスト.txt"
    out = tmp_path / "out"
    SevenZip().extract(target, out)
    assert (out / "日本語のテスト.txt").read_bytes() == b"payload"


def test_encrypted_legacy_name_and_utf8_password_fallback(make_legacy_zip, tmp_path):
    archive = make_legacy_zip(password="中文密码")
    settings = Settings(filename_encoding="cp932", passwords=["wrong", "中文密码"])
    result = UnpackTask([archive], settings).run()
    assert (result.output / "日本語のテスト.txt").read_bytes() == b"payload"
    target = tmp_path / "normalized.zip"
    assert normalize_zip_names(archive, target, "cp932", Event())
    output = tmp_path / "fallback"
    output.mkdir()
    SevenZip()._extract_utf8_zip(target, output, "中文密码")
    assert (output / "日本語のテスト.txt").read_bytes() == b"payload"


def test_setting_change_restarts_cached_layers(make_legacy_zip, make_archive):
    inner = make_archive("7z", "correct", headers=True)
    outer = make_legacy_zip(files={"内側.7z": inner.read_bytes(), "説明.txt": b"sidecar"})
    task = UnpackTask([outer], Settings(filename_encoding="cp1251", passwords=["wrong"]))
    with pytest.raises(PasswordRequired):
        task.run()
    assert task.archive_count == 1
    old_nodes = task.nodes
    task.settings.filename_encoding = "cp932"
    # Keep it paused to verify the older evidence is not deleted on restart.
    with pytest.raises(PasswordRequired):
        task.run()
    assert old_nodes.exists() and task.nodes != old_nodes and task.archive_count == 1
    task.settings.passwords.append("correct")
    result = task.run()
    assert (result.output / "説明.txt").read_bytes() == b"sidecar"
    assert result.archives == 2


def test_legacy_zip_pseudo_encryption_still_repairs(make_legacy_zip):
    from test_core import fake_flags
    archive = make_legacy_zip()
    fake_flags(archive)
    archive.write_bytes(b"\xff\xd8carrier\xff\xd9" + archive.read_bytes())
    archive = archive.rename(archive.with_suffix(".jpg"))
    before = archive.read_bytes()
    result = UnpackTask([archive], Settings(filename_encoding="cp932")).run()
    assert (result.output / "日本語のテスト.txt").read_bytes() == b"payload"
    assert archive.read_bytes() == before


def test_numeric_split_legacy_zip(make_legacy_zip, tmp_path):
    archive = make_legacy_zip()
    data = archive.read_bytes()
    parts = [tmp_path / "legacy.zip.001", tmp_path / "legacy.zip.002"]
    parts[0].write_bytes(data[:70])
    parts[1].write_bytes(data[70:])
    result = UnpackTask(parts, Settings(filename_encoding="cp932")).run()
    assert (result.output / "日本語のテスト.txt").read_bytes() == b"payload"


def test_pkzip_split_legacy_names(tmp_path):
    name = "日本語A.bin"
    parts = []
    for source in sorted((Path(__file__).parent / "fixtures/zip_split").glob("split.*")):
        target = tmp_path / source.name
        target.write_bytes(source.read_bytes().replace(b"payload.bin", name.encode("cp932")))
        parts.append(target)
    result = UnpackTask(parts, Settings(filename_encoding="cp932")).run()
    assert (result.output / name).stat().st_size == 150000


@pytest.mark.parametrize("valid_crc", [True, False])
def test_existing_unicode_extra_precedence(make_legacy_zip, valid_crc):
    name = "日本語のテスト.txt"
    unicode_name = "既存の正しい名前.txt"
    crc = zlib.crc32(name.encode("cp932")) ^ (0 if valid_crc else 1)
    value = b"\x01" + struct.pack("<I", crc) + unicode_name.encode("utf-8")
    archive = make_legacy_zip(extra=struct.pack("<HH", 0x7075, len(value)) + value)
    result = UnpackTask([archive], Settings(filename_encoding="cp932")).run()
    assert (result.output / (unicode_name if valid_crc else name)).read_bytes() == b"payload"


def test_unicode_extra_traversal_rejected(make_legacy_zip, tmp_path):
    archive = make_legacy_zip(files={"../日本語.txt": b"must not escape"}, password="中文密码")
    normalized = tmp_path / "normalized.zip"
    normalize_zip_names(archive, normalized, "cp932", Event())
    with pytest.raises(UnsafeArchive):
        SevenZip().extract(normalized, tmp_path / "engine-out", "中文密码")
    fallback = tmp_path / "fallback"
    fallback.mkdir()
    with pytest.raises(UnsafeArchive):
        SevenZip()._extract_utf8_zip(normalized, fallback, "中文密码")
    assert not (tmp_path / "日本語.txt").exists()


def test_normalization_cancel_and_existing_target(make_legacy_zip, tmp_path):
    archive = make_legacy_zip()
    target = tmp_path / "existing.zip"
    target.write_bytes(b"do not overwrite")
    with pytest.raises(FileExistsError):
        normalize_zip_names(archive, target, "cp932", Event())
    assert target.read_bytes() == b"do not overwrite"
    cancel = Event()
    cancel.set()
    with pytest.raises(Cancelled):
        normalize_zip_names(archive, tmp_path / "cancelled.zip", "cp932", cancel)
    assert not (tmp_path / "cancelled.zip").exists()


def test_filename_encoding_config_roundtrip_and_legacy(tmp_path):
    (tmp_path / "settings.json").write_text(json.dumps({"passwords": ["secret"]}))
    assert load_settings(tmp_path).filename_encoding == "default"
    settings = Settings(filename_encoding="cp932")
    save_settings(settings, tmp_path)
    assert load_settings(tmp_path).filename_encoding == "cp932"
    with pytest.raises(InvalidInput, match="文件名编码"):
        Settings(filename_encoding="unsupported-codepage").validate()
