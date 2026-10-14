from pathlib import Path
from threading import Event

import pytest

from conftest import zip_bytes
from neo_unpack.model import Cancelled, MissingVolume, PasswordRequired, Settings
from neo_unpack.workflow import UnpackTask


def files_under(root):
    return {p.relative_to(root).as_posix(): p.read_bytes() for p in root.rglob('*') if p.is_file()}


@pytest.mark.parametrize('mode', ['direct', 'folder', 'fixed', 'fixed_folder'])
def test_parallel_directories_keep_groups_and_unwrap_each_branch(make_zip, tmp_path, mode):
    source = make_zip('outer.zip', {
        '合集/A/包装/inner.jpg': b'image prefix' + zip_bytes({'包内/app.exe': b'a', '包内/assets/data.bin': b'asset-a'}),
        '合集/A/说明.txt': b'a-note',
        '合集/B/inner.zip': zip_bytes({'app.exe': b'b', 'assets/data.bin': b'asset-b'}),
        '合集/说明.txt': b'collection-note', '说明.txt': b'outer-note',
        '合集/__MACOSX/junk': b'ignored',
    })
    fixed = tmp_path / 'fixed'
    result = UnpackTask([source], Settings(output_mode=mode, fixed_directory=str(fixed))).run()
    assert result.output == {'direct': tmp_path, 'folder': tmp_path / 'outer',
                             'fixed': fixed, 'fixed_folder': fixed / '合集'}[mode]
    actual = files_under(result.output)
    if mode == 'direct':
        actual.pop('outer.zip')
    assert actual == {'A/app.exe': b'a', 'A/assets/data.bin': b'asset-a', 'A/说明.txt': b'a-note',
                      'B/app.exe': b'b', 'B/assets/data.bin': b'asset-b',
                      '说明.txt': b'outer-note', '说明_outer.txt': b'collection-note'}
    assert (result.archives, result.layers) == (3, 2)
    assert source.exists()


@pytest.mark.parametrize('bad_first', [False, True], ids=['bad-last', 'bad-first'])
@pytest.mark.parametrize('bad_kind', ['image', 'document', 'mixed', 'mixed-missing-volume', 'missing-volume'])
def test_all_siblings_preflight_before_any_inner_extraction(make_zip, bad_first, bad_kind):
    inner = zip_bytes({'result.bin': b'done'})
    good, bad = ('B', 'A') if bad_first else ('A', 'B')
    blocked = {'image': {'cover.png': b'image'},
               'document': {'book.docx': zip_bytes({'word/document.xml': 'document'})},
               'mixed': {'inner.zip': inner, 'child/inner.zip': inner},
               'mixed-missing-volume': {'archive.7z.002': b'part-two', 'child/inner.zip': inner},
               'missing-volume': {'archive.7z.002': b'part-two'}}[bad_kind]
    contents = {f'{good}/wrapper/inner.zip': inner, '说明.txt': b'note'}
    contents.update({f'{bad}/deep/{name}': data for name, data in blocked.items()})
    source = make_zip(files=contents)
    task = UnpackTask([source], Settings())
    if bad_kind == 'missing-volume':
        with pytest.raises(MissingVolume):
            task.run()
        assert source.exists() and task.archive_count == 1 and len(task.prepared) == 1
        return
    result = task.run()
    assert result.archives == result.layers == 1
    assert len(task.prepared) == 1
    assert files_under(result.output) == contents


def test_nested_forks_keep_each_level_but_peel_single_wrappers(make_zip):
    source = make_zip(files={
        'A/wrapper/X/inner.zip': zip_bytes({'x.bin': b'x'}),
        'A/wrapper/Y/deeper/inner.zip': zip_bytes({'next.zip': zip_bytes({'y.bin': b'y'})}),
        'A/note.txt': b'a', 'A/wrapper/note.txt': b'wrapper',
        'B/inner.zip': zip_bytes({'b.bin': b'b'}),
    })
    result = UnpackTask([source], Settings()).run()
    assert files_under(result.output) == {'A/X/x.bin': b'x', 'A/Y/y.bin': b'y',
                                         'A/note.txt': b'a', 'A/note_sample.txt': b'wrapper', 'B/b.bin': b'b'}
    assert (result.archives, result.layers) == (5, 3)


def test_text_empty_branches_and_empty_archive_are_preserved(make_zip):
    source = make_zip(files={'A/inner.zip': zip_bytes({}), 'B/empty/': b'',
                             'C/wrapper/note.txt': b'note', 'D/inner.zip': zip_bytes({'d.bin': b'd'})})
    result = UnpackTask([source], Settings()).run()
    assert (result.output / 'A').is_dir() and not list((result.output / 'A').iterdir())
    assert (result.output / 'B/empty').is_dir()
    assert files_under(result.output) == {'C/wrapper/note.txt': b'note', 'D/d.bin': b'd'}
    assert result.archives == 3


def test_renamed_txt_archive_in_branch_is_extracted(make_zip):
    inner = zip_bytes({'done.bin': b'done'})
    source = make_zip(files={'A/archive.txt': inner, 'A/note.txt': b'note', 'B/inner.zip': inner})
    result = UnpackTask([source], Settings()).run()
    assert files_under(result.output) == {'A/done.bin': b'done', 'A/note.txt': b'note', 'B/done.bin': b'done'}


def test_renamed_txt_archive_beside_directory_stops_whole_level(make_zip):
    inner = zip_bytes({'done.bin': b'done'})
    contents = {'archive.txt': inner, 'A/inner.zip': inner}
    source = make_zip(files=contents)
    result = UnpackTask([source], Settings()).run()
    assert files_under(result.output) == contents and result.archives == 1


def test_one_layer_does_not_enter_parallel_folders(make_zip):
    inner = zip_bytes({'done.bin': b'done'})
    contents = {'A/inner.zip': inner, 'B/inner.zip': inner}
    source = make_zip(files={f'wrapper/{name}': data for name, data in contents.items()})
    result = UnpackTask([source], Settings(depth='one')).run()
    assert files_under(result.output) == contents and result.archives == 1


def test_same_named_folders_from_separate_archives_never_merge(make_zip):
    def collection(value):
        return zip_bytes({'A/inner.zip': zip_bytes({'app.exe': value, 'assets/data.bin': value}),
                          'B/inner.zip': zip_bytes({'data.bin': value})})
    source = make_zip(files={'first.zip': collection(b'first'), 'second.zip': collection(b'second')})
    result = UnpackTask([source], Settings()).run()
    assert files_under(result.output) == {
        'A/app.exe': b'first', 'A/assets/data.bin': b'first', 'B/data.bin': b'first',
        'A (2)/app.exe': b'second', 'A (2)/assets/data.bin': b'second', 'B (2)/data.bin': b'second'}


def test_existing_output_and_multiple_apps_within_branch_are_safe(make_zip, tmp_path):
    fixed = tmp_path / 'fixed'
    (fixed / 'A').mkdir(parents=True)
    (fixed / 'A/keep.bin').write_bytes(b'keep')
    app = lambda value: zip_bytes({'app.exe': value, 'assets/data.bin': value})
    source = make_zip(files={'A/first.zip': app(b'first'), 'A/second.zip': app(b'second'),
                             'B/inner.zip': zip_bytes({'b.bin': b'b'})})
    result = UnpackTask([source], Settings(output_mode='fixed', fixed_directory=str(fixed))).run()
    assert files_under(result.output) == {'A/keep.bin': b'keep', 'A_结果/app.exe': b'first',
        'A_结果/assets/data.bin': b'first', 'A_结果/second/app.exe': b'second',
        'A_结果/second/assets/data.bin': b'second', 'B/b.bin': b'b'}


def test_volumes_group_only_inside_each_directory(make_archive, make_zip):
    parts = make_archive('7z', split=100, files={'done.bin': b'done' * 100})
    assert len(parts) > 1
    contents = {f'{branch}/{p.name}': p.read_bytes() for branch in ['A', 'B'] for p in parts}
    source = make_zip(files=contents)
    result = UnpackTask([source], Settings()).run()
    assert files_under(result.output) == {f'{branch}/done.bin': b'done' * 100 for branch in ['A', 'B']}
    assert result.archives == 3
    divided = {f'{"A" if i == 0 else "B"}/{p.name}': p.read_bytes() for i, p in enumerate(parts)}
    source = make_zip('divided.zip', files=divided)
    task = UnpackTask([source], Settings())
    with pytest.raises(MissingVolume):
        task.run()
    assert task.archive_count == 1 and source.exists()


@pytest.mark.parametrize('change_encoding', [False, True], ids=['reuse', 'rebuild-encoding'])
def test_password_retry_reuses_branches_and_keeps_counts(make_archive, make_zip, tmp_path, change_encoding):
    first = make_archive('7z', 'first', headers=True, name='first', files={'app.exe': b'a'})
    second = make_archive('7z', 'second', headers=True, name='second', files={'app.exe': b'b'})
    source = make_zip(files={'A/inner.7z': first.read_bytes(), 'B/inner.7z': second.read_bytes()})
    fixed = tmp_path / 'fixed'
    task = UnpackTask([source], Settings(passwords=['first'], output_mode='fixed', fixed_directory=str(fixed), delete_sources=True))
    with pytest.raises(PasswordRequired):
        task.run()
    assert source.exists() and not fixed.exists()
    assert task.archive_count == 2 and task.settings.password_success_counts == {'first': 1}
    cached = tuple(task.extracted.values())
    assert any((p / 'A/inner.7z').is_file() for p in cached)
    task.settings.passwords.append('second')
    if change_encoding:
        task.settings.filename_encoding = 'cp932'
    result = task.run()
    assert files_under(result.output) == {'A/app.exe': b'a', 'B/app.exe': b'b'}
    assert result.archives == 3 and result.deleted_sources == 1
    assert task.settings.password_success_counts == {'first': 2 if change_encoding else 1, 'second': 1}
    assert (set(cached) <= set(task.extracted.values())) is (not change_encoding)


def test_cancel_during_preflight_keeps_source_and_can_resume(make_zip, monkeypatch):
    inner = zip_bytes({'done.bin': b'done'})
    source = make_zip(files={'A/inner.zip': inner, 'B/inner.zip': inner})
    cancel = Event()
    task = UnpackTask([source], Settings(delete_sources=True), cancel=cancel)
    original = task._plan_directory
    def interrupt(root):
        if root.name == 'B':
            cancel.set()
        return original(root)
    monkeypatch.setattr(task, '_plan_directory', interrupt)
    with pytest.raises(Cancelled):
        task.run()
    assert source.exists() and task.session.exists() and task.archive_count == 1
    cancel.clear()
    monkeypatch.setattr(task, '_plan_directory', original)
    result = task.run()
    assert result.archives == 3 and not source.exists()
    assert files_under(result.output) == {'A/done.bin': b'done', 'B/done.bin': b'done'}


def test_publish_failure_rolls_back_branches_and_retry_uses_cache(make_zip, tmp_path, monkeypatch):
    inner = zip_bytes({'done.bin': b'done'})
    source = make_zip(files={'A/inner.zip': inner, 'B/inner.zip': inner})
    fixed = tmp_path / 'fixed'
    fixed.mkdir()
    (fixed / 'keep.bin').write_bytes(b'keep')
    task = UnpackTask([source], Settings(output_mode='fixed', fixed_directory=str(fixed), delete_sources=True))
    original = Path.rename
    moves = []
    def fail_second(path, target):
        if path.parent.name.startswith('.neo-publish-') and target.parent == fixed:
            moves.append(target)
            if len(moves) == 2:
                raise OSError('simulated publish failure')
        return original(path, target)
    monkeypatch.setattr(Path, 'rename', fail_second)
    with pytest.raises(OSError, match='simulated'):
        task.run()
    assert files_under(fixed) == {'keep.bin': b'keep'} and source.exists()
    assert task.archive_count == 3
    monkeypatch.setattr(Path, 'rename', original)
    result = task.run()
    assert result.archives == 3 and not source.exists()
    assert files_under(fixed) == {'keep.bin': b'keep', 'A/done.bin': b'done', 'B/done.bin': b'done'}


@pytest.mark.parametrize('bad_first', [False, True], ids=['image-last', 'image-first'])
def test_non_archive_sibling_stops_before_missing_volume_error(make_zip, bad_first):
    image, missing = ('A', 'B') if bad_first else ('B', 'A')
    contents = {f'{image}/cover.png': b'image', f'{missing}/inner.7z.002': b'part-two'}
    source = make_zip(files=contents)
    result = UnpackTask([source], Settings()).run()
    assert result.archives == 1 and files_under(result.output) == contents


def test_directory_depth_does_not_consume_archive_depth_and_keeps_intermediates(make_zip):
    inner = zip_bytes({'done.bin': b'done'})
    deep = 'w/' * 70
    contents = {f'A/{deep}inner.zip': inner, 'B/inner.zip': inner}
    source = make_zip(files=contents)
    result = UnpackTask([source], Settings(keep_intermediates=True)).run()
    assert (result.archives, result.layers) == (3, 2)
    assert (result.output / 'A/done.bin').read_bytes() == b'done'
    intermediate = result.output / '_中间产物/001_sample/解压结果'
    assert files_under(intermediate) == contents
