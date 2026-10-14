import json
import shutil
import sys

import pytest

from neo_unpack import cli, config
from neo_unpack.engine import PasswordCheck
from neo_unpack.model import InvalidInput, PasswordRequired, Settings
from neo_unpack.workflow import UnpackTask


def test_legacy_config_and_counts_roundtrip(tmp_path):
    (tmp_path / "settings.json").write_text(json.dumps({"passwords": ["old"]}))
    assert config.load_settings(tmp_path).password_success_counts == {}
    settings = Settings(passwords=["old", " 中文 "], password_success_counts={"old": 12, " 中文 ": 3})
    config.save_settings(settings, tmp_path)
    assert config.load_settings(tmp_path) == settings


@pytest.mark.parametrize("counts", [[], None, {"p": -1}, {"p": True}, {"p": 1.5}, {"p": "1"}, {"": 1}],
                         ids=["list", "null", "negative", "bool", "float", "string", "empty-password"])
def test_invalid_counts_are_rejected(counts):
    with pytest.raises(InvalidInput, match="成功次数"):
        Settings(password_success_counts=counts).validate()


def test_frequency_order_and_stable_ties_without_duplicate_candidates(make_zip, monkeypatch):
    settings = Settings(passwords=["low", "tie-first", "high", "tie-last", "high", ""],
                        password_success_counts={"high": 9, "tie-first": 3, "tie-last": 3})
    task = UnpackTask([make_zip()], settings)
    calls = []

    def reject(archive, destination, password=None):
        calls.append(password)
        raise PasswordRequired("需要密码")

    monkeypatch.setattr(task.engine, "check_password", lambda *_: PasswordCheck.UNKNOWN)
    monkeypatch.setattr(task.engine, "extract", reject)
    try:
        with pytest.raises(PasswordRequired):
            task.run()
        assert calls == [None, "high", "tie-first", "tie-last", "low"]
        assert settings.password_success_counts == {"high": 9, "tie-first": 3, "tie-last": 3}
    finally:
        shutil.rmtree(task.session)


def test_recent_low_frequency_success_does_not_override_high_frequency(make_archive):
    inner = make_archive("7z", "high", headers=True, name="inner")
    outer = make_archive("7z", "low", headers=True, name="outer", files={"inner.7z": inner.read_bytes()})
    settings = Settings(passwords=["low", "high"], password_success_counts={"high": 8})
    task = UnpackTask([outer], settings)
    original = task.engine.extract
    calls = []

    def record(archive, destination, password=None):
        calls.append((task.current_depth, password))
        return original(archive, destination, password)

    task.engine.extract = record
    result = task.run()
    assert (result.output / "结果.bin").is_file()
    assert calls == [(1, None), (1, "high"), (1, "low"), (2, None), (2, "high")]
    assert settings.password_success_counts == {"high": 9, "low": 1}


def test_each_archive_counts_once_including_middle_layer_and_branches(make_archive, tmp_path):
    a = make_archive("7z", "secret", headers=True, name="a", files={"a.bin": b"a", "aa.bin": b"aa"})
    b = make_archive("7z", "secret", headers=True, name="b", files={"b.bin": b"b", "bb.bin": b"bb"})
    outer = make_archive("7z", "secret", headers=True, name="outer", files={"a.7z": a.read_bytes(), "b.7z": b.read_bytes()})
    settings = Settings(passwords=["secret"])
    snapshots = []

    def save(password):
        config.save_settings(settings, tmp_path / "config")
        snapshots.append(config.load_settings(tmp_path / "config").password_success_counts[password])

    result = UnpackTask([outer], settings, on_password_success=save).run()
    assert result.archives == 3 and len(list(result.output.iterdir())) == 4
    assert snapshots == [1, 2, 3]
    assert config.load_settings(tmp_path / "config").password_success_counts == {"secret": 3}


def test_order_is_recomputed_after_each_layer(make_archive):
    inner = make_archive("7z", "correct", headers=True, name="inner")
    outer = make_archive("7z", "correct", headers=True, name="outer", files={"inner.7z": inner.read_bytes()})
    task = UnpackTask([outer], Settings(passwords=["wrong", "correct"]))
    extract = task.engine.extract
    calls = []

    def record(archive, destination, password=None):
        calls.append(password)
        return extract(archive, destination, password)

    task.engine.extract = record
    task.run()
    assert calls == [None, "wrong", "correct", None, "correct"]
    assert task.settings.password_success_counts == {"correct": 2}


def test_encrypted_volumes_count_as_one_archive(make_archive):
    volumes = make_archive("7z", "correct", split=100, files={"result.bin": b"payload" * 100})
    settings = Settings(passwords=["correct"])
    result = UnpackTask(volumes, settings).run()
    assert len(volumes) > 1 and result.archives == 1
    assert settings.password_success_counts == {"correct": 1}


def test_failed_inner_archive_keeps_counts_and_resume_does_not_recount_cache(make_archive, tmp_path):
    inner = make_archive("7z", "inner-secret", headers=True, name="inner")
    outer = make_archive("7z", "outer-secret", headers=True, name="outer", files={"inner.7z": inner.read_bytes()})
    settings = Settings(passwords=["wrong", "outer-secret"])
    task = UnpackTask([outer], settings, on_password_success=lambda _: config.save_settings(settings, tmp_path / "config"))
    with pytest.raises(PasswordRequired):
        task.run()
    assert config.load_settings(tmp_path / "config").password_success_counts == {"outer-secret": 1}
    manifest = (task.session / "task.json").read_text("utf-8")
    assert "password_success_counts" not in manifest and "outer-secret" not in manifest and "wrong" not in manifest
    settings.passwords.append("inner-secret")
    result = task.run()
    assert result.archives == 2
    assert settings.password_success_counts == {"outer-secret": 1, "inner-secret": 1}
    with pytest.raises(InvalidInput):
        task.run()
    assert settings.password_success_counts == {"outer-secret": 1, "inner-secret": 1}


def test_plain_archive_does_not_count_passwords(make_zip):
    settings = Settings(passwords=["unused"], password_success_counts={"unused": 2})
    calls = []
    UnpackTask([make_zip()], settings, on_password_success=calls.append).run()
    assert settings.password_success_counts == {"unused": 2} and calls == []


def test_statistics_save_failure_does_not_invalidate_extraction(make_archive):
    source = make_archive("7z", "correct", headers=True)
    settings = Settings(passwords=["correct"])
    messages = []

    def fail_save(password):
        raise OSError("private exception detail")

    result = UnpackTask([source], settings, log=messages.append, on_password_success=fail_save).run()
    assert (result.output / "结果.bin").is_file()
    assert settings.password_success_counts == {"correct": 1}
    assert any("成功次数保存失败" in message for message in messages)
    assert all("private exception detail" not in message for message in messages)


@pytest.mark.parametrize("password", ["library", "temporary"])
def test_cli_persists_library_counts_without_saving_overrides(make_archive, tmp_path, monkeypatch, password):
    monkeypatch.setattr(config, "config_directory", lambda: tmp_path / "config")
    settings = Settings(passwords=["library"], password_success_counts={"library": 4})
    config.save_settings(settings)
    path = make_archive("7z", password, headers=True)
    password_file = tmp_path / "passwords.txt"
    password_file.write_text(password, encoding="utf-8")
    monkeypatch.setattr(sys, "argv", ["neo-unpack-cli", str(path), "--output", str(tmp_path / "fixed"),
                                    "--one-layer", "--password-file", str(password_file), "--filename-encoding", "cp932"])
    assert cli.main() == 0
    reloaded = config.load_settings()
    assert reloaded.password_success_counts == {"library": 5 if password == "library" else 4}
    assert reloaded.passwords == ["library"]
    assert reloaded.output_mode == "folder" and reloaded.fixed_directory == "" and reloaded.depth == "auto"
    assert reloaded.filename_encoding == "default"
