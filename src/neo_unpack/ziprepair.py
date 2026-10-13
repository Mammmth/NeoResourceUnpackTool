from __future__ import annotations

import struct
import zipfile
import zlib
import lzma
from pathlib import Path
from threading import Event

from .model import Cancelled


def _has_aes(extra: bytes) -> bool:
    position = 0
    while position + 4 <= len(extra):
        tag, length = struct.unpack_from("<HH", extra, position)
        if tag == 0x9901:
            return True
        position += 4 + length
    return False


def repair_zip_copy(path: Path, cancel: Event | None = None) -> int:
    """Repair only proven fake bit-0 encryption, on a caller-owned disposable COPY.

    Entries are verified to EOF (CRC/size) after the two flag fields are changed.
    A real encrypted entry is restored byte-for-byte if validation fails. This
    also permits archives containing both fake and genuinely encrypted entries.
    """
    try:
        with zipfile.ZipFile(path) as archive:
            infos = archive.infolist()
            central = archive.start_dir
    except (zipfile.BadZipFile, OSError, NotImplementedError):
        return 0
    repaired = 0
    with path.open("r+b") as stream:
        for index, info in enumerate(infos):
            if cancel and cancel.is_set():
                raise Cancelled("已取消。")
            stream.seek(central)
            header = stream.read(46)
            if len(header) != 46 or header[:4] != b"PK\x01\x02":
                break
            name_size, extra_size, comment_size = struct.unpack_from("<HHH", header, 28)
            next_central = central + 46 + name_size + extra_size + comment_size
            central_flag_pos = central + 8
            central_flags = struct.unpack_from("<H", header, 8)[0]
            stream.seek(info.header_offset)
            local = stream.read(30)
            if len(local) < 30 or local[:4] != b"PK\x03\x04":
                central = next_central
                continue
            local_flags = struct.unpack_from("<H", local, 6)[0]
            flags = central_flags | local_flags
            if not flags & 1 or flags & 64 or info.compress_type == 99 or _has_aes(info.extra):
                central = next_central
                continue
            patches = ((central_flag_pos, central_flags), (info.header_offset + 6, local_flags))
            for position, value in patches:
                stream.seek(position)
                stream.write(struct.pack("<H", value & ~1))
            stream.flush()
            valid = False
            try:
                with zipfile.ZipFile(path) as candidate:
                    with candidate.open(candidate.infolist()[index]) as member:
                        total = 0
                        while data := member.read(1024 * 1024):
                            if cancel and cancel.is_set():
                                raise Cancelled("已取消。")
                            total += len(data)
                        valid = total == info.file_size
            except Cancelled:
                raise
            except (zipfile.BadZipFile, RuntimeError, NotImplementedError, OSError, EOFError, ValueError, zlib.error, lzma.LZMAError):
                pass
            finally:
                if not valid:
                    for position, value in patches:
                        stream.seek(position)
                        stream.write(struct.pack("<H", value))
                    stream.flush()
            if valid:
                repaired += 1
            central = next_central
    return repaired
