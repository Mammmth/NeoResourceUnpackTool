import importlib.util
import io
import tarfile
from importlib.metadata import PackagePath
from pathlib import Path
from types import SimpleNamespace

import pytest


def script(name):
    path = Path(__file__).resolve().parents[1] / 'scripts' / f'{name}.py'
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_package_notices_preserve_nested_names_and_copyright(tmp_path):
    source = tmp_path / 'installed'
    paths = ['pkg.dist-info/licenses/LICENSE', 'pkg.dist-info/licenses/vendor/LICENSE',
             'pkg.dist-info/NOTICE', 'pkg/__init__.py']
    for path in paths:
        target = source / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text('original ' + path, encoding='utf-8')
    distribution = SimpleNamespace(files=[PackagePath(p) for p in paths],
                                   locate_file=lambda p: source / p)
    destination = tmp_path / 'legal'
    script('collect_licenses').copy_package_licenses(distribution, destination)
    assert (destination / 'vendor/LICENSE').read_text() == 'original ' + paths[1]
    assert (destination / 'NOTICE').read_text() == 'original ' + paths[2]
    assert not (destination / '__init__.py').exists()


@pytest.mark.parametrize('missing', [False, True], ids=['complete-notices', 'missing-notice-blocked'])
def test_qt_snapshot_includes_referenced_copyright_or_fails(tmp_path, missing):
    archive = tmp_path / 'source.tar.xz'
    files = {'src/lib/qt_attribution.json': '{"LicenseFile": "COPYRIGHT"}',
             'LICENSES/MIT.txt': 'MIT terms', 'src/lib/COPYRIGHT': 'Copyright upstream'}
    if missing:
        del files['src/lib/COPYRIGHT']
    with tarfile.open(archive, 'w:xz') as output:
        for path, content in files.items():
            data = content.encode()
            member = tarfile.TarInfo('source/' + path)
            member.size = len(data)
            output.addfile(member, io.BytesIO(data))
    module = script('refresh_qt_notices')
    component = {'name': 'test', 'url': 'https://example.invalid/source', 'sha256': 'test'}
    if missing:
        with pytest.raises(ValueError, match='Missing attribution license'):
            module.render_notices(archive, component)
    else:
        text = module.render_notices(archive, component)
        assert 'Copyright upstream' in text and 'MIT terms' in text
