import shutil
from pathlib import Path

import pytest

from neo_unpack.model import MissingVolume, Settings, UnpackError
from neo_unpack.workflow import UnpackTask

FIXTURES = Path(__file__).parent / "fixtures" / "rar"


@pytest.mark.parametrize("name", ["rar3-solid.rar", "rar5-solid.rar", "rar3-comment-hpsw.rar", "rar5-hpsw.rar", "rar5-psw.rar"])
@pytest.mark.parametrize("prefix", [b"", b"\xff\xd8image bytes\xff\xd9"])
def test_rar_formats(tmp_path, name, prefix):
    path = tmp_path / "picture.jpg"
    path.write_bytes(prefix + (FIXTURES / name).read_bytes())
    result = UnpackTask([path], Settings(passwords=["wrong", "password"])).run()
    assert list(result.output.rglob("*.txt"))


@pytest.mark.parametrize("version", [3, 5])
def test_rar_volumes(tmp_path, version):
    paths = []
    for path in sorted(FIXTURES.glob(f"rar{version}-vols.part*.rar")):
        target = tmp_path / path.name
        shutil.copyfile(path, target)
        paths.append(target)
    result = UnpackTask(paths[::-1], Settings()).run()
    assert (result.output / "bigfile.txt").stat().st_size == 205000
    assert (result.output / "smallfile.txt").stat().st_size == 2050


def test_old_rar_volumes(tmp_path):
    paths = []
    for index, path in enumerate(sorted(FIXTURES.glob("rar3-vols.part*.rar"))):
        target = tmp_path / ("old.rar" if index == 0 else f"old.r{index - 1:02d}")
        shutil.copyfile(path, target)
        paths.append(target)
    result = UnpackTask(paths, Settings()).run()
    assert (result.output / "bigfile.txt").stat().st_size == 205000


def test_missing_last_rar_volume(tmp_path):
    paths = []
    for name in ["rar5-vols.part1.rar", "rar5-vols.part2.rar"]:
        target = tmp_path / name
        shutil.copyfile(FIXTURES / name, target)
        paths.append(target)
    with pytest.raises(MissingVolume):
        UnpackTask(paths, Settings()).run()
