from __future__ import annotations

import io
import struct
import zipfile
import zlib
from pathlib import Path
from threading import Event

from .model import ArchiveInfo, Cancelled, InvalidInput

SIGNATURES = {
    b"PK\x03\x04": "zip", b"PK\x05\x06": "zip", b"PK\x07\x08": "zip",
    b"7z\xbc\xaf\x27\x1c": "7z", b"Rar!\x1a\x07\x00": "rar",
    b"Rar!\x1a\x07\x01\x00": "rar",
}


class OffsetReader(io.RawIOBase):
    """Seekable archive view without loading the carrier into memory."""

    def __init__(self, path: Path, offset: int):
        super().__init__()
        self.stream = path.open("rb")
        self.offset = offset
        self.stream.seek(offset)

    def read(self, size=-1):
        return self.stream.read(size)

    def seek(self, offset, whence=0):
        if whence == 0:
            offset += self.offset
        return self.stream.seek(offset, whence) - self.offset

    def tell(self):
        return self.stream.tell() - self.offset

    def seekable(self):
        return True

    def readable(self):
        return True

    def close(self):
        self.stream.close()
        super().close()


def _valid(path: Path, offset: int, signature: bytes, split: bool) -> bool:
    with path.open("rb") as stream:
        stream.seek(offset)
        header = stream.read(65536)
    kind = SIGNATURES[signature]
    if kind == "7z":
        return len(header) >= 32 and zlib.crc32(header[12:32]) == int.from_bytes(header[8:12], "little")
    if kind == "rar":
        if signature.endswith(b"\x01\x00"):
            data = header[8:]
            size = 0
            for index, byte in enumerate(data[4:14]):
                size |= (byte & 127) << (index * 7)
                if byte < 128:
                    end = 5 + index + size
                    return end <= len(data) and zlib.crc32(data[4:end]) == int.from_bytes(data[:4], "little")
            return False
        data = header[7:]
        if len(data) < 7 or data[2] != 0x73:
            return False
        size = int.from_bytes(data[5:7], "little")
        return 7 <= size <= len(data) and zlib.crc32(data[2:size]) & 65535 == int.from_bytes(data[:2], "little")
    if split:
        return header.startswith((b"PK\x03\x04", b"PK\x07\x08"))
    try:
        with OffsetReader(path, offset) as view, zipfile.ZipFile(view) as archive:
            infos = archive.infolist()
            # Require a genuine beginning, not a member's signature inside another archive.
            if infos:
                return min(i.header_offset for i in infos) == 0
            return header.startswith(b"PK\x05\x06")
    except (OSError, ValueError, zipfile.BadZipFile, NotImplementedError, struct.error):
        return False


def detect(path: Path, *, split: bool = False, cancel: Event | None = None) -> ArchiveInfo | None:
    """Find a structurally plausible archive, then let the engine validate its payload."""
    overlap = b""
    position = 0
    checked = set()
    with path.open("rb") as stream:
        while chunk := stream.read(4 * 1024 * 1024):
            if cancel and cancel.is_set():
                raise Cancelled("已取消。")
            data = overlap + chunk
            base = position - len(overlap)
            candidates = []
            for signature in SIGNATURES:
                start = 0
                while (index := data.find(signature, start)) >= 0:
                    candidates.append((base + index, signature))
                    start = index + 1
            for offset, signature in sorted(candidates):
                if (offset, signature) in checked:
                    continue
                checked.add((offset, signature))
                if len(checked) > 8192:
                    raise InvalidInput("文件中的压缩包候选过多，无法可靠定位；原文件未修改。")
                if _valid(path, offset, signature, split):
                    return ArchiveInfo(SIGNATURES[signature], offset)
            position += len(chunk)
            overlap = data[-7:]
    return None
