from __future__ import annotations

import re
from pathlib import Path

from .model import InvalidInput, MissingVolume, VolumeGroup


def _part(path: Path) -> tuple[str, str, int] | None:
    name = path.name
    if match := re.fullmatch(r"(.+)\.part(\d+)\.rar", name, re.I):
        return "rar_part", match[1].casefold(), int(match[2])
    if match := re.fullmatch(r"(.+)\.(\d{3,})", name, re.I):
        return "numeric", match[1].casefold(), int(match[2])
    if match := re.fullmatch(r"(.+)\.r(\d{2,})", name, re.I):
        return "rar_old", match[1].casefold(), int(match[2]) + 1
    if match := re.fullmatch(r"(.+)\.z(\d{2,})", name, re.I):
        return "zip_split", match[1].casefold(), int(match[2])
    return None


def is_volume_name(path: Path) -> bool:
    return _part(path) is not None


def make_group(paths: list[Path] | tuple[Path, ...]) -> VolumeGroup:
    if not paths:
        raise InvalidInput("请拖入一个文件，或一次拖入同一组的全部分卷。")
    normalized = tuple(Path(p).absolute() for p in paths)
    if len(set(normalized)) != len(normalized):
        raise InvalidInput("选择中存在重复文件。")
    for path in normalized:
        if path.is_symlink() or not path.is_file():
            raise InvalidInput("只能选择普通文件，不能选择文件夹或符号链接。")
    parts = [(p, _part(p)) for p in normalized]
    marked = [part for _, part in parts if part is not None]
    if not marked:
        if len(normalized) != 1:
            raise InvalidInput("不支持批量任务：请只拖入一个压缩包或同一组分卷。")
        return VolumeGroup(normalized, name=normalized[0].stem)
    style, stem, _ = marked[0]
    indexed: list[tuple[int, Path]] = []
    for path, part in parts:
        if part is not None and part[:2] == (style, stem):
            indexed.append((part[2], path))
        elif style == "rar_old" and path.name.casefold() == stem + ".rar":
            indexed.append((0, path))
        elif style == "zip_split" and path.name.casefold() == stem + ".zip":
            indexed.append((10**12, path))
        else:
            raise InvalidInput("拖入的文件不属于同一组分卷；一次只能处理一组。")
    indexed.sort(key=lambda item: item[0])
    numbers = [n for n, _ in indexed]
    start = 0 if style == "rar_old" else 1
    regular = numbers[:-1] if style == "zip_split" and numbers[-1] == 10**12 else numbers
    if regular != list(range(start, start + len(regular))):
        raise MissingVolume("分卷缺少首卷、编号不连续或存在重复编号；请一起拖入全部分卷。")
    if style == "zip_split" and numbers[-1] != 10**12:
        raise MissingVolume("缺少 ZIP 分卷的最后一个 .zip 文件，请一起拖入。")
    name = re.sub(r"\.(?:7z|zip|rar)$", "", stem, flags=re.I)
    return VolumeGroup(tuple(p for _, p in indexed), style, name)


def discover_groups(files: list[Path]) -> list[VolumeGroup]:
    """Group only explicitly supplied files (or files from a task's own extraction)."""
    buckets: dict[tuple[Path, str, str], list[Path]] = {}
    ordinary = []
    for file in files:
        part = _part(file)
        if part:
            buckets.setdefault((file.parent, part[0], part[1]), []).append(file)
        else:
            ordinary.append(file)
    singles = []
    for file in ordinary:
        key = None
        suffix = file.suffix.casefold()
        if suffix in {".rar", ".zip"}:
            key = (file.parent, "rar_old" if suffix == ".rar" else "zip_split", file.stem.casefold())
        if key in buckets:
            buckets[key].append(file)
        else:
            singles.append(VolumeGroup((file,), name=file.stem))
    return [make_group(v) for v in buckets.values()] + singles
