import json
import os
from pathlib import Path
from threading import Event

import pytest

from neo_unpack.config import load_settings, save_settings
from neo_unpack.model import Cancelled, InvalidInput, PasswordRequired, Settings
from neo_unpack.workflow import UnpackTask


@pytest.mark.parametrize('mode', ['folder', 'direct', 'fixed', 'fixed_folder'])
def test_delete_only_selected_source_after_publish(make_zip, tmp_path, mode):
    source = make_zip('input.zip', {'result.bin': b'result'})
    neighbor = make_zip('neighbor.zip')
    messages = []
    task = UnpackTask([source], Settings(output_mode=mode, fixed_directory=str(tmp_path / 'output'),
                                       delete_sources=True, keep_intermediates=True), log=messages.append)
    result = task.run()
    assert not source.exists() and neighbor.exists()
    assert (result.output / 'result.bin').read_bytes() == b'result'
    assert list((result.output / '_中间产物').rglob('*.zip'))
    assert result.deleted_sources == 1 and not result.warnings
    assert any('已删除原文件' in line for line in messages)


def test_default_preserves_source(make_zip):
    source = make_zip()
    result = UnpackTask([source], Settings()).run()
    assert source.exists() and result.deleted_sources == 0


def test_deletes_all_selected_volumes_only(make_archive, tmp_path):
    sources = make_archive('7z', split=300, files={'payload.bin': os.urandom(1200)})
    unrelated = tmp_path / 'unrelated.7z.001'
    unrelated.write_bytes(b'keep')
    result = UnpackTask(sources, Settings(delete_sources=True)).run()
    assert result.deleted_sources == len(sources) > 1
    assert all(not source.exists() for source in sources)
    assert unrelated.read_bytes() == b'keep'
    assert (result.output / 'payload.bin').stat().st_size == 1200


def test_password_failure_then_resume_deletes_only_after_success(make_archive, make_zip):
    inner = make_archive('7z', 'secret', headers=True)
    source = make_zip('outer.zip', {'inner.7z': inner.read_bytes()})
    task = UnpackTask([source], Settings(delete_sources=True))
    with pytest.raises(PasswordRequired):
        task.run()
    assert source.exists() and task.session.exists()
    task.settings.passwords = ['secret']
    result = task.run()
    assert not source.exists()
    assert inner.exists()  # External files used to create the sample are not inputs.
    assert (result.output / '结果.bin').exists()


@pytest.mark.parametrize('failure', ['cancel', 'publish', 'manifest'])
def test_failed_or_cancelled_task_keeps_sources(make_zip, monkeypatch, failure):
    source = make_zip()
    cancel = Event()
    task = UnpackTask([source], Settings(delete_sources=True), cancel=cancel)
    if failure == 'cancel':
        cancel.set()
        expected = Cancelled
    elif failure == 'publish':
        monkeypatch.setattr(task, '_publish', lambda _: (_ for _ in ()).throw(OSError('disk full')))
        expected = OSError
    else:
        original = task.save_manifest
        def fail_success(status):
            if status == '完成':
                raise OSError('disk full')
            original(status)
        monkeypatch.setattr(task, 'save_manifest', fail_success)
        expected = OSError
    with pytest.raises(expected):
        task.run()
    assert source.exists()


def test_source_replaced_during_extraction_is_not_deleted(make_zip, monkeypatch):
    source = make_zip()
    task = UnpackTask([source], Settings(delete_sources=True))
    original_publish = task._publish
    original_stat = source.stat()
    def publish_and_replace(finals):
        result = original_publish(finals)
        replacement = source.with_name('replacement.tmp')
        replacement.write_bytes(b'x' * original_stat.st_size)
        os.utime(replacement, ns=(original_stat.st_atime_ns, original_stat.st_mtime_ns))
        replacement.replace(source)
        return result
    monkeypatch.setattr(task, '_publish', publish_and_replace)
    result = task.run()
    assert source.exists() and source.read_bytes().startswith(b'x')
    assert (result.output / 'hello.txt').exists()
    assert result.warnings and result.deleted_sources == 0 and result.session.exists()


def test_delete_failure_keeps_results_and_reports_partial_cleanup(make_archive, monkeypatch):
    sources = make_archive('7z', split=300, files={'payload.bin': os.urandom(1200)})
    blocked = sources[-1]
    original_unlink = Path.unlink
    def unlink(path, *args, **kwargs):
        if path == blocked:
            raise PermissionError('locked')
        return original_unlink(path, *args, **kwargs)
    monkeypatch.setattr(Path, 'unlink', unlink)
    task = UnpackTask(sources, Settings(delete_sources=True))
    result = task.run()
    assert blocked.exists()
    assert result.deleted_sources == len(sources) - 1
    assert (result.output / 'payload.bin').exists()
    assert result.warnings and result.session.exists()
    assert '原文件未删除' in (result.session / 'process.log').read_text('utf-8')
    with pytest.raises(InvalidInput, match='已完成'):
        task.run()


def test_delete_config_default_roundtrip_and_type_validation(tmp_path):
    (tmp_path / 'settings.json').write_text(json.dumps({'passwords': []}))
    assert load_settings(tmp_path).delete_sources is False
    settings = Settings(delete_sources=True)
    save_settings(settings, tmp_path)
    assert load_settings(tmp_path).delete_sources is True
    with pytest.raises(InvalidInput):
        Settings(delete_sources='false').validate()


def test_source_name_collision_does_not_delete_published_content(make_zip):
    source = make_zip('input.zip', {'input.zip': b'final content'})
    result = UnpackTask([source], Settings(output_mode='direct', delete_sources=True)).run()
    assert not source.exists()
    assert any(path.read_bytes() == b'final content' for path in result.output.glob('*.zip'))


@pytest.mark.parametrize('during_preflight', [False, True])
def test_cancel_just_before_cleanup_preserves_source(make_zip, monkeypatch, during_preflight):
    source = make_zip()
    cancel = Event()
    def progress(event):
        if event.stage == '删除原文件' and not during_preflight:
            cancel.set()
    task = UnpackTask([source], Settings(delete_sources=True), cancel=cancel, progress=progress)
    same_source = task._same_source
    def check_source(path, original):
        if task._finished and during_preflight:
            cancel.set()
        return same_source(path, original)
    monkeypatch.setattr(task, '_same_source', check_source)
    result = task.run()
    assert source.exists() and result.deleted_sources == 0 and result.warnings
    assert (result.output / 'hello.txt').exists()


@pytest.mark.parametrize('flag,deleted', [('--delete-sources', True), ('--no-delete-sources', False)])
def test_cli_deletion_override(make_zip, tmp_path, monkeypatch, flag, deleted):
    import sys
    from neo_unpack import cli, config
    monkeypatch.setattr(config, 'config_directory', lambda: tmp_path / 'config')
    config.save_settings(Settings(delete_sources=not deleted))
    source = make_zip()
    monkeypatch.setattr(sys, 'argv', ['neo-unpack-cli', str(source), flag])
    assert cli.main() == 0
    assert source.exists() is not deleted
