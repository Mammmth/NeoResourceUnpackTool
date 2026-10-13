"""Portable legacy ZIP filename decoding, without recompressing encrypted data.

7-Zip's POSIX build does not implement Windows code pages like CP932. Supply
validated Info-ZIP Unicode Path (0x7075) fields in the disposable central
directory instead. Local headers, compressed bytes and encryption stay intact.
"""
from __future__ import annotations

import struct
import zipfile
import zlib
from pathlib import Path
from threading import Event

from .model import Cancelled, FILENAME_ENCODINGS, Log, UnpackError


def _extra_fields(extra: bytes):
    at = 0
    while at + 4 <= len(extra):
        tag, size = struct.unpack_from("<HH", extra, at)
        end = at + 4 + size
        if end > len(extra):
            raise UnpackError("ZIP 文件名附加字段不完整，无法安全转换编码。")
        yield tag, extra[at + 4:end]
        at = end
    if at != len(extra):
        raise UnpackError("ZIP 文件名附加字段不完整，无法安全转换编码。")


def unicode_path(raw: bytes, extra: bytes) -> str | None:
    for tag, value in _extra_fields(extra):
        if tag != 0x7075 or len(value) < 5 or value[0] != 1:
            continue
        if struct.unpack_from("<I", value, 1)[0] != zlib.crc32(raw):
            continue
        try:
            name = value[5:].decode("utf-8")
        except UnicodeDecodeError:
            continue
        if name and "\x00" not in name:
            return name
    return None


def zip_member_name(member) -> str:
    """Match 7-Zip's Unicode metadata precedence in the pyzipper fallback."""
    if member.flag_bits & 0x800:
        return member.orig_filename
    raw = member.orig_filename.encode("cp437")
    return unicode_path(raw, member.extra) or member.orig_filename


def _guess_encoding(names: list[bytes]) -> str | None:
    # ASCII carries no evidence; a strict UTF-8 round trip is tried first.
    if not names:
        return None
    try:
        for name in names:
            name.decode("utf-8")
        return "utf-8"
    except UnicodeDecodeError:
        pass
    from charset_normalizer import from_bytes

    codecs = [key for key in FILENAME_ENCODINGS if key not in {"default", "auto", "utf-8"}]
    sample = bytearray()
    for name in names:
        if len(sample) + len(name) + 1 > 65536:
            break
        sample.extend(name + b"\n")
    if not sample:
        return None
    matches = list(from_bytes(bytes(sample), cp_isolation=codecs, enable_fallback=False))
    if not matches:
        return None
    best = matches[0]
    if best.encoding not in codecs or best.chaos > 0.15:
        return None
    # Short names are often ambiguous. Do not silently pick between equally
    # plausible code pages merely because the detector ranked one first.
    if len(matches) > 1:
        second = matches[1]
        if second.chaos - best.chaos < 0.10 and best.coherence - second.coherence < 0.20:
            return None
    try:
        for name in names:
            name.decode(best.encoding)
    except UnicodeDecodeError:
        return None
    return best.encoding


def _read(stream, size: int) -> bytes:
    data = stream.read(size)
    if len(data) != size:
        raise UnpackError("ZIP 目录不完整，无法转换文件名编码。")
    return data


def normalize_zip_names(source: Path, target: Path, encoding: str, cancel: Event,
                        log: Log = lambda _: None) -> bool:
    """Write a separate copy if legacy names need a Unicode path field."""
    if encoding == "default":
        return False
    try:
        with zipfile.ZipFile(source) as archive:
            members = archive.infolist()
            central_start = archive.start_dir
            comment = archive.comment
    except (zipfile.BadZipFile, UnicodeError, NotImplementedError) as error:
        raise UnpackError("ZIP 文件名目录无法读取，不能应用所选编码。") from error
    legacy = {}
    for index, member in enumerate(members):
        if cancel.is_set():
            raise Cancelled("已取消。")
        if member.flag_bits & 0x800:
            continue
        raw = member.orig_filename.encode("cp437")
        if not raw.isascii() and unicode_path(raw, member.extra) is None:
            legacy[index] = raw
    if not legacy:
        return False
    if encoding == "auto":
        encoding = _guess_encoding(list(legacy.values()))
        if encoding is None:
            log("文件名编码自动检测不确定，保留引擎默认处理；如有乱码，请手动选择对应语言后重新解压。")
            return False
        log(f"自动推测 ZIP 文件名编码：{FILENAME_ENCODINGS[encoding]}。")
    else:
        log(f"ZIP 文件名编码：{FILENAME_ENCODINGS[encoding]}。")
    names = {}
    try:
        for index, raw in legacy.items():
            name = raw.decode(encoding)
            # A replacement character indicates already-lost source data.
            if "\x00" in name or "\ufffd" in name:
                raise UnicodeError()
            names[index] = name.encode("utf-8")
    except UnicodeError as error:
        raise UnpackError("所选编码无法完整解读 ZIP 文件名，请更换文件名编码后重试。") from error

    created = False
    try:
        with source.open("rb") as reader, target.open("xb") as writer:
            created = True
            left = central_start
            while left:
                if cancel.is_set():
                    raise Cancelled("已取消。")
                chunk = _read(reader, min(left, 4 * 1024 * 1024))
                writer.write(chunk)
                left -= len(chunk)
            for index in range(len(members)):
                if cancel.is_set():
                    raise Cancelled("已取消。")
                header = bytearray(_read(reader, 46))
                if header[:4] != b"PK\x01\x02":
                    raise UnpackError("ZIP 中央目录结构异常，无法转换文件名编码。")
                n, e, c = struct.unpack_from("<HHH", header, 28)
                raw, extra, note = _read(reader, n), _read(reader, e), _read(reader, c)
                if index in names:
                    if raw != legacy[index]:
                        raise UnpackError("ZIP 文件名目录发生变化，已停止处理。")
                    extra = b"".join(struct.pack("<HH", tag, len(value)) + value
                                     for tag, value in _extra_fields(extra) if tag != 0x7075)
                    value = b"\x01" + struct.pack("<I", zlib.crc32(raw)) + names[index]
                    if len(extra) + len(value) + 4 > 65535:
                        raise UnpackError("ZIP 文件名附加字段过长，无法应用所选编码。")
                    extra += struct.pack("<HH", 0x7075, len(value)) + value
                    struct.pack_into("<H", header, 30, len(extra))
                writer.write(header + raw + extra + note)
            size = writer.tell() - central_start
            count = len(members)
            u32 = 0xFFFFFFFF
            # Entry ZIP64 fields and local-header offsets remain unchanged.
            # Regenerate the end records because the central directory grew.
            if count >= 65535 or central_start >= u32 or size >= u32 or any(m.extract_version >= 45 for m in members):
                end = writer.tell()
                writer.write(struct.pack("<4sQHHIIQQQQ", b"PK\x06\x06", 44, 45, 45, 0, 0, count, count, size, central_start))
                writer.write(struct.pack("<4sIQI", b"PK\x06\x07", 0, end, 1))
            writer.write(struct.pack("<4s4H2IH", b"PK\x05\x06", 0, 0, min(count, 65535),
                                     min(count, 65535), min(size, u32), min(central_start, u32), len(comment)))
            writer.write(comment)
    except BaseException:
        if created:
            target.unlink(missing_ok=True)
        raise
    return True
