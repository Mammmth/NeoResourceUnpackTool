import io
import os
import subprocess
import zipfile
from pathlib import Path

import pytest

from neo_unpack.engine import find_engine


@pytest.fixture
def engine():
    return find_engine()


def zip_bytes(files: dict[str, bytes | str]) -> bytes:
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, content in files.items():
            archive.writestr(name, content)
    return stream.getvalue()


@pytest.fixture
def make_zip(tmp_path):
    def make(name="sample.zip", files=None, prefix=b""):
        path = tmp_path / name
        path.write_bytes(prefix + zip_bytes(files or {"hello.txt": "hello"}))
        return path
    return make


@pytest.fixture
def make_archive(tmp_path, engine):
    def make(kind="7z", password=None, headers=False, split=None, name="archive", files=None):
        source = tmp_path / (name + "_source")
        source.mkdir(exist_ok=True)
        for member, data in (files or {"结果.bin": b"payload" * 100}).items():
            path = source / member
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data if isinstance(data, bytes) else data.encode())
        archive = tmp_path / f"{name}.{kind}"
        if kind == "zip" and password and not password.isascii() and not split:
            import pyzipper
            with pyzipper.AESZipFile(archive, "w", compression=pyzipper.ZIP_DEFLATED, encryption=pyzipper.WZ_AES) as z:
                z.setpassword(password.encode("utf-8"))
                for path in source.rglob("*"):
                    if path.is_file():
                        z.write(path, str(path.relative_to(source)))
            return archive
        args = [str(engine), "a", "-t" + kind, str(archive), ".", "-y"]
        if password:
            args.append("-p" + password)
        if headers:
            args.append("-mhe=on")
        if split:
            args.append("-v" + str(split) + "b")
        subprocess.run(args, cwd=source, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, check=True)
        return sorted(tmp_path.glob(f"{name}.{kind}.*")) if split else archive
    return make


@pytest.fixture
def make_legacy_zip(tmp_path):
    def make(codec="cp932", files=None, password=None, name="legacy.zip", zip64=False, extra=b""):
        import pyzipper
        from pyzipper.zipfile_aes import AESZipInfo

        base = AESZipInfo if password else zipfile.ZipInfo

        class LegacyInfo(base):
            def _encodeFilenameFlags(self):
                return self.filename.encode(codec), self.flag_bits & ~0x800

        path = tmp_path / name
        kind = pyzipper.AESZipFile if password else zipfile.ZipFile
        with kind(path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            archive.comment = b"keep archive comment"
            if password:
                archive.zipinfo_cls = LegacyInfo
                archive.setencryption(pyzipper.WZ_AES)
                archive.setpassword(password.encode("utf-8"))
            for member, content in (files or {"日本語のテスト.txt": b"payload"}).items():
                info = LegacyInfo(member)
                info.extra = extra
                info.compress_type = zipfile.ZIP_DEFLATED
                info.comment = b"keep entry comment"
                if zip64:
                    with archive.open(info, "w", force_zip64=True) as writer:
                        writer.write(content)
                else:
                    archive.writestr(info, content)
        return path
    return make
