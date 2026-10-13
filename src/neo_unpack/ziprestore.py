"""Normalize genuine PKZIP disks to one ZIP before pseudo-encryption repair."""
from __future__ import annotations

import struct
from pathlib import Path
from threading import Event

from .model import Cancelled, MissingVolume, UnpackError

U32 = 0xFFFFFFFF


def _read(stream, size):
    data = stream.read(size)
    if len(data) != size:
        raise MissingVolume("ZIP 分卷数据不完整。")
    return data


def join_zip_disks(paths: list[Path], target: Path, cancel: Event) -> None:
    offsets = []
    length = 0
    with target.open("w+b") as merged:
        for source in paths:
            offsets.append(length)
            with source.open("rb") as part:
                while chunk := part.read(4 * 1024 * 1024):
                    if cancel.is_set():
                        raise Cancelled("已取消。")
                    merged.write(chunk)
                    length += len(chunk)
        merged.seek(max(0, length - 65557))
        tail = merged.read()
        pos = tail.rfind(b"PK\x05\x06")
        while pos >= 0:
            if pos + 22 <= len(tail) and pos + 22 + int.from_bytes(tail[pos + 20:pos + 22], "little") == len(tail):
                break
            pos = tail.rfind(b"PK\x05\x06", 0, pos)
        if pos < 0:
            raise MissingVolume("ZIP 分卷缺少有效的结束记录。")
        end = length - len(tail) + pos
        _, disk, cd_disk, disk_count, total, cd_size, cd_offset, comment_length = struct.unpack_from("<4s4H2IH", tail, pos)
        comment = tail[pos + 22:]
        original_zip64 = disk == 65535 or cd_disk == 65535 or total == 65535 or cd_offset == U32 or cd_size == U32
        if end >= 20:
            merged.seek(end - 20)
            locator = merged.read(20)
            if locator[:4] == b"PK\x06\x07":
                original_zip64 = True
                _, end_disk, end_offset, disks = struct.unpack("<4sIQI", locator)
                if end_disk >= len(offsets) or disks != len(offsets):
                    raise MissingVolume("ZIP64 分卷数量与拖入文件不一致。")
                merged.seek(offsets[end_disk] + end_offset)
                header = _read(merged, 56)
                if header[:4] != b"PK\x06\x06":
                    raise UnpackError("ZIP64 分卷结束记录无效。")
                disk, cd_disk, disk_count, total, cd_size, cd_offset = struct.unpack_from("<IIQQQQ", header, 16)
        if disk != len(offsets) - 1 or cd_disk >= len(offsets):
            raise MissingVolume("ZIP 分卷数量与压缩包记录不一致。")
        central_start = offsets[cd_disk] + cd_offset
        if central_start >= end or total > 1000000:
            raise UnpackError("ZIP 分卷目录结构异常。")
        central_file = target.with_suffix(".central.tmp")
        try:
            with central_file.open("w+b") as directory:
                merged.seek(central_start)
                for _ in range(total):
                    if cancel.is_set():
                        raise Cancelled("已取消。")
                    header = bytearray(_read(merged, 46))
                    if header[:4] != b"PK\x01\x02":
                        raise UnpackError("ZIP 分卷中央目录不完整。")
                    name_length, extra_length, note_length, member_disk = struct.unpack_from("<HHHH", header, 28)
                    packed, unpacked = struct.unpack_from("<II", header, 20)
                    member_offset = struct.unpack_from("<I", header, 42)[0]
                    name = _read(merged, name_length)
                    extra = _read(merged, extra_length)
                    note = _read(merged, note_length)
                    others = bytearray()
                    zip64 = b""
                    at = 0
                    while at + 4 <= len(extra):
                        tag, size = struct.unpack_from("<HH", extra, at)
                        data = extra[at + 4:at + 4 + size]
                        if len(data) != size:
                            raise UnpackError("ZIP 附加字段不完整。")
                        if tag == 1:
                            zip64 = data
                        else:
                            others.extend(extra[at:at + 4 + size])
                        at += 4 + size
                    zpos = 0
                    values = []
                    for value, sentinel, width in [(unpacked, U32, 8), (packed, U32, 8), (member_offset, U32, 8), (member_disk, 65535, 4)]:
                        if value == sentinel:
                            if zpos + width > len(zip64):
                                raise UnpackError("ZIP64 条目字段不完整。")
                            value = int.from_bytes(zip64[zpos:zpos + width], "little")
                            zpos += width
                        values.append(value)
                    unpacked_value, packed_value, member_offset, member_disk = values
                    if member_disk >= len(offsets):
                        raise MissingVolume("ZIP 条目引用了未提供的分卷。")
                    absolute = offsets[member_disk] + member_offset
                    if absolute >= central_start:
                        raise UnpackError("ZIP 条目偏移异常。")
                    fields = bytearray()
                    if unpacked == U32:
                        fields.extend(struct.pack("<Q", unpacked_value))
                    if packed == U32:
                        fields.extend(struct.pack("<Q", packed_value))
                    if absolute >= U32:
                        fields.extend(struct.pack("<Q", absolute))
                    struct.pack_into("<H", header, 34, 0)
                    struct.pack_into("<I", header, 42, min(absolute, U32))
                    if fields:
                        others = bytearray(struct.pack("<HH", 1, len(fields))) + fields + others
                        struct.pack_into("<H", header, 6, max(45, struct.unpack_from("<H", header, 6)[0]))
                    if len(others) > 65535:
                        raise UnpackError("ZIP 附加字段过长。")
                    struct.pack_into("<H", header, 30, len(others))
                    directory.write(header + name + others + note)
                size = directory.tell()
                merged.seek(central_start)
                directory.seek(0)
                while chunk := directory.read(4 * 1024 * 1024):
                    merged.write(chunk)
                if original_zip64 or total >= 65535 or central_start >= U32 or size >= U32:
                    zip64_end = merged.tell()
                    merged.write(struct.pack("<4sQHHIIQQQQ", b"PK\x06\x06", 44, 45, 45, 0, 0, total, total, size, central_start))
                    merged.write(struct.pack("<4sIQI", b"PK\x06\x07", 0, zip64_end, 1))
                merged.write(struct.pack("<4s4H2IH", b"PK\x05\x06", 0, 0, min(total, 65535), min(total, 65535), min(size, U32), min(central_start, U32), comment_length))
                merged.write(comment)
                merged.truncate()
        finally:
            central_file.unlink(missing_ok=True)
