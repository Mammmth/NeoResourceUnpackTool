import hashlib
import shutil
import struct
from threading import Event

import pyzipper
import pytest

from neo_unpack.engine import PasswordCheck, SevenZip
from neo_unpack.model import Cancelled, PasswordRequired, Settings, UnpackError, UnsafeArchive
from neo_unpack.workflow import UnpackTask


@pytest.fixture
def aes_zip(tmp_path):
    path = tmp_path / "encrypted.zip"
    with pyzipper.AESZipFile(path, "w", encryption=pyzipper.WZ_AES) as archive:
        archive.setpassword(b"correct")
        archive.writestr("large.bin", b"x" * (4 * 1024 * 1024))
        archive.writestr("other.bin", b"other")
    return path


@pytest.mark.parametrize("kind", ["aes", "zipcrypto"])
def test_header_check_reads_no_payload(aes_zip, make_archive, monkeypatch, kind):
    path = aes_zip if kind == "aes" else make_archive("zip", "correct")
    shared_file = pyzipper.zipfile._SharedFile
    original_read = shared_file.read
    bytes_read = []

    def read(stream, size=-1):
        data = original_read(stream, size)
        bytes_read.append(len(data))
        return data

    monkeypatch.setattr(shared_file, "read", read)
    engine = SevenZip()
    assert engine.check_password(path, "correct") is PasswordCheck.POSSIBLE
    assert sum(bytes_read) < 256  # Local header + name/extra + encryption header.
    # ZipCrypto has a one-byte check, so select a rejected candidate rather than
    # making this test flaky when a wrong password happens to pass the header.
    assert any(engine.check_password(path, f"wrong-{i}") is PasswordCheck.MISMATCH for i in range(8))


@pytest.mark.parametrize("case", ["unicode", "plain", "7z", "broken", "unsupported"])
def test_uncertain_checks_remain_unknown(aes_zip, make_zip, make_archive, monkeypatch, case):
    path, password = aes_zip, "correct"
    if case == "unicode":
        path, password = make_archive("zip", "中文密码"), "中文密码"
    elif case == "plain":
        path = make_zip()
    elif case == "7z":
        path = make_archive("7z", "correct", headers=True)
    elif case == "broken":
        path.write_bytes(b"PK truncated")
    elif case == "unsupported":
        def unsupported(*args, **kwargs):
            raise NotImplementedError("unsupported encryption")
        monkeypatch.setattr(pyzipper.AESZipFile, "open", unsupported)
    assert SevenZip().check_password(path, password) is PasswordCheck.UNKNOWN


def record_extractions(task, monkeypatch):
    calls = []
    extract = task.engine.extract

    def recording(archive, destination, password=None):
        calls.append(password)
        return extract(archive, destination, password)

    monkeypatch.setattr(task.engine, "extract", recording)
    return calls


def test_wrong_password_is_skipped_and_correct_password_is_fully_extracted(aes_zip, monkeypatch):
    before = hashlib.sha256(aes_zip.read_bytes()).digest()
    events, messages = [], []
    wrong = next(p for p in (f"wrong-{i}" for i in range(8))
                 if SevenZip().check_password(aes_zip, p) is PasswordCheck.MISMATCH)
    task = UnpackTask([aes_zip], Settings(passwords=[wrong, "correct"]), progress=events.append, log=messages.append)
    calls = record_extractions(task, monkeypatch)
    result = task.run()
    assert calls == [None, "correct"]
    assert (result.output / "large.bin").read_bytes() == b"x" * (4 * 1024 * 1024)
    assert hashlib.sha256(aes_zip.read_bytes()).digest() == before
    assert any(event.stage == "检查密码" and event.percent is None for event in events)
    assert any(event.stage == "正在解压" and event.percent == 0 for event in events)
    start = next(i for i, line in enumerate(messages) if line.startswith("正在解压"))
    success = next(i for i, line in enumerate(messages) if "密码（correct）验证成功" in line)
    assert start < success
    assert all("wrong" not in line for line in messages)


@pytest.mark.parametrize("later_error", [False, True], ids=["all-screened", "later-uncertain-error"])
def test_false_rejection_recovers_with_original_path(aes_zip, monkeypatch, later_error):
    messages = []
    passwords = ["correct", "later"] if later_error else ["correct"]
    task = UnpackTask([aes_zip], Settings(passwords=passwords), log=messages.append)
    extract = task.engine.extract
    calls = []

    def check(archive, password):
        return PasswordCheck.MISMATCH if password == "correct" else PasswordCheck.UNKNOWN

    def recording(archive, destination, password=None):
        calls.append(password)
        if password == "later":
            raise UnpackError("结构校验失败")
        return extract(archive, destination, password)

    monkeypatch.setattr(task.engine, "check_password", check)
    monkeypatch.setattr(task.engine, "extract", recording)
    result = task.run()
    assert (result.output / "large.bin").stat().st_size == 4 * 1024 * 1024
    assert calls == ([None, "later", "correct"] if later_error else [None, "correct"])
    assert any("按原流程复试" in line for line in messages)
    assert task.settings.password_success_counts == {"correct": 1}


def test_all_wrong_candidates_are_retried_and_failure_keeps_sources(aes_zip, monkeypatch):
    task = UnpackTask([aes_zip], Settings(passwords=["wrong-one", "wrong-two"], delete_sources=True))
    monkeypatch.setattr(task.engine, "check_password", lambda *args: PasswordCheck.MISMATCH)
    calls = record_extractions(task, monkeypatch)
    try:
        with pytest.raises(PasswordRequired):
            task.run()
        assert calls == [None, "wrong-one", "wrong-two"]
        assert aes_zip.exists() and task.session.exists()
        assert not task.extracted and not task.settings.password_success_counts
        assert not any("验证成功" in line for line in task._log_lines)
        assert "wrong-one" not in (task.session / "process.log").read_text("utf-8")
    finally:
        shutil.rmtree(task.session, ignore_errors=True)


def test_header_pass_does_not_publish_corrupt_data(aes_zip):
    # Damage the payload, keeping the salt/password verifier intact.
    with pyzipper.AESZipFile(aes_zip) as archive:
        member = archive.infolist()[0]
    with aes_zip.open("r+b") as stream:
        stream.seek(member.header_offset + 26)
        name_size, extra_size = struct.unpack("<HH", stream.read(4))
        position = member.header_offset + 30 + name_size + extra_size + 18 + 100
        stream.seek(position)
        value = stream.read(1)[0]
        stream.seek(position)
        stream.write(bytes([value ^ 1]))
    assert SevenZip().check_password(aes_zip, "correct") is PasswordCheck.POSSIBLE
    task = UnpackTask([aes_zip], Settings(passwords=["correct"], delete_sources=True))
    try:
        with pytest.raises(UnpackError):
            task.run()
        assert aes_zip.exists() and not (aes_zip.parent / "encrypted").exists()
        assert not task.settings.password_success_counts and not task.extracted
    finally:
        shutil.rmtree(task.session, ignore_errors=True)


def test_unknown_check_preserves_unicode_password_fallback(make_archive, monkeypatch):
    path = make_archive("zip", "中文密码")
    task = UnpackTask([path], Settings(passwords=["中文密码"]))
    run = task.engine.run
    fallback = task.engine._extract_utf8_zip
    fallbacks = []

    def windows_password_behavior(args, *a, **kw):
        if args[0] == "x":
            return 2, "Wrong password"
        return run(args, *a, **kw)

    def record_fallback(*args):
        fallbacks.append(True)
        return fallback(*args)

    monkeypatch.setattr(task.engine, "run", windows_password_behavior)
    monkeypatch.setattr(task.engine, "_extract_utf8_zip", record_fallback)
    result = task.run()
    assert (result.output / "结果.bin").is_file() and fallbacks == [True]


@pytest.mark.parametrize("during_check", [False, True], ids=["before-check", "during-check"])
def test_screening_remains_cancellable(aes_zip, monkeypatch, during_check):
    cancel = Event()
    if during_check:
        original = pyzipper.AESZipFile.open

        def open_then_cancel(*args, **kwargs):
            result = original(*args, **kwargs)
            cancel.set()
            return result

        monkeypatch.setattr(pyzipper.AESZipFile, "open", open_then_cancel)
    else:
        cancel.set()
    with pytest.raises(Cancelled):
        SevenZip(cancel=cancel).check_password(aes_zip, "correct")


def test_cancel_does_not_start_compatibility_retry(aes_zip, monkeypatch):
    task = UnpackTask([aes_zip], Settings(passwords=["correct", "cancel"], delete_sources=True))
    calls = record_extractions(task, monkeypatch)

    def check(archive, password):
        if password == "cancel":
            raise Cancelled("已取消")
        return PasswordCheck.MISMATCH

    monkeypatch.setattr(task.engine, "check_password", check)
    try:
        with pytest.raises(Cancelled):
            task.run()
        assert calls == [None] and aes_zip.exists()
    finally:
        shutil.rmtree(task.session, ignore_errors=True)


def test_screen_pass_never_bypasses_path_checks(tmp_path):
    path = tmp_path / "unsafe.zip"
    with pyzipper.AESZipFile(path, "w", encryption=pyzipper.WZ_AES) as archive:
        archive.setpassword(b"correct")
        archive.writestr("../escape.bin", b"unsafe")
    destination = tmp_path / "output"
    engine = SevenZip()
    assert engine.check_password(path, "correct") is PasswordCheck.POSSIBLE
    with pytest.raises(UnsafeArchive):
        engine.extract(path, destination, "correct")
    assert not destination.exists() and not (tmp_path.parent / "escape.bin").exists()
