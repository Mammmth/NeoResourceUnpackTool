import random
import shutil
import struct
from pathlib import Path

from neo_unpack.model import Settings
from neo_unpack.workflow import UnpackTask
import pytest


@pytest.mark.parametrize("fake", [False, True])
def test_pkzip_spanned(tmp_path, fake):
    sources = sorted((Path(__file__).parent / "fixtures/zip_split").glob("split.*"))
    paths = []
    for source in sources:
        target = tmp_path / source.name
        shutil.copyfile(source, target)
        if fake:
            data = bytearray(target.read_bytes())
            for signature, delta in [(b"PK\x03\x04", 6), (b"PK\x01\x02", 8)]:
                index = data.find(signature)
                if index >= 0:
                    flags = struct.unpack_from("<H", data, index + delta)[0]
                    struct.pack_into("<H", data, index + delta, flags | 1)
            target.write_bytes(data)
        paths.append(target)
    result = UnpackTask(paths[::-1], Settings()).run()
    assert (result.output / "payload.bin").read_bytes() == random.Random(42).randbytes(150000)
