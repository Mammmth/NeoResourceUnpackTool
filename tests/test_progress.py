import sys
from pathlib import Path
from threading import Event

import pytest

from neo_unpack.engine import SevenZip
from neo_unpack.model import Cancelled, PasswordRequired, Settings
from neo_unpack.workflow import UnpackTask


@pytest.mark.parametrize('cancel', [False, True], ids=['live-progress', 'cancel'])
def test_progress_arrives_before_process_exit_and_remains_cancellable(tmp_path, cancel):
    marker = tmp_path / 'finished'
    script = tmp_path / 'emitter.py'
    script.write_text('''import sys, time
from pathlib import Path
sys.stderr.write('  2')
sys.stderr.flush()
time.sleep(0.3)
sys.stderr.write('7% 1 - file99%.bin')
sys.stderr.flush()
time.sleep(0.4)
sys.stderr.write('\\b' * 30 + ' 64%\\r')
sys.stderr.flush()
time.sleep(0.4)
Path(sys.argv[1]).touch()
print('done')
''')
    events = []
    stopped = Event()

    def progress(value):
        events.append((value, marker.exists()))
        if cancel:
            stopped.set()

    engine = SevenZip(Path(sys.executable), stopped, progress)
    if cancel:
        with pytest.raises(Cancelled):
            engine.run([str(script), str(marker)], track_progress=True)
        assert not marker.exists()
        assert events == [(27, False)]
    else:
        code, output = engine.run([str(script), str(marker)], track_progress=True)
        assert code == 0 and 'done' in output
        assert events == [(27, False), (64, False)]
        assert marker.exists()


def test_real_recursive_progress_and_success_password_in_disk_log(make_archive):
    inner = make_archive('7z', 'inner-secret', headers=True, name='inner')
    outer = make_archive('7z', 'outer-secret', headers=True, name='outer', files={'inner.7z': inner.read_bytes()})
    events, messages = [], []
    task = UnpackTask([outer], Settings(passwords=['wrong', 'outer-secret']), progress=events.append, log=messages.append)
    with pytest.raises(PasswordRequired):
        task.run()
    success = '密码（outer-secret）验证成功，后续层将优先尝试。'
    assert success in messages
    assert success in (task.session / 'process.log').read_text('utf-8')
    assert 'outer-secret' not in (task.session / 'task.json').read_text('utf-8')
    assert all('wrong' not in message for message in messages)
    assert any(p.depth == 1 and p.percent == 100 for p in events)
    assert not any(p.depth == 2 and p.percent == 100 for p in events)
    events.clear()
    task.settings.passwords.append('inner-secret')
    result = task.run()
    assert (result.output / '结果.bin').is_file()
    assert any(p.depth == 2 and p.percent == 100 for p in events)
    assert any('复用已成功' in message for message in messages)
    assert events[-1].stage == '整理结果'


def test_utf8_fallback_reports_bytes_without_false_completion(make_archive, tmp_path):
    archive = make_archive('zip', '中文密码', files={'large.bin': b'x' * (3 * 1024 * 1024)})
    events = []
    engine = SevenZip(progress=events.append)
    destination = tmp_path / 'fallback'
    destination.mkdir()
    engine._extract_utf8_zip(archive, destination, '中文密码')
    assert (destination / 'large.bin').stat().st_size == 3 * 1024 * 1024
    assert events[0] == 0 and any(0 < p < 99 for p in events)
    assert 100 not in events  # extract() owns final validation and completion.
    events.clear()
    with pytest.raises(PasswordRequired):
        engine._extract_utf8_zip(archive, destination, '错误')
    assert 100 not in events
