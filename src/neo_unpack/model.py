from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable


class UnpackError(Exception):
    """A user-facing failure; messages must never include passwords."""


class InvalidInput(UnpackError):
    pass


class MissingVolume(UnpackError):
    pass


class UnsafeArchive(UnpackError):
    pass


class Cancelled(UnpackError):
    pass


class PasswordRequired(UnpackError):
    pass


@dataclass
class Settings:
    output_mode: str = "folder"
    fixed_directory: str = ""
    depth: str = "auto"
    keep_intermediates: bool = False
    passwords: list[str] = field(default_factory=list)

    def validate(self) -> None:
        if not isinstance(self.fixed_directory, str) or not isinstance(self.keep_intermediates, bool):
            raise InvalidInput("配置文件中的目录或产物保留选项格式不正确。")
        if not isinstance(self.passwords, list) or not all(isinstance(p, str) for p in self.passwords):
            raise InvalidInput("密码库格式不正确。")
        if self.output_mode not in {"direct", "folder", "fixed", "fixed_folder"}:
            raise InvalidInput("未知的解压位置选项。")
        if self.depth not in {"one", "auto"}:
            raise InvalidInput("未知的递归模式。")
        if self.output_mode.startswith("fixed") and not self.fixed_directory.strip():
            raise InvalidInput("请先选择固定输出目录。")


@dataclass(frozen=True)
class ArchiveInfo:
    kind: str
    offset: int


@dataclass(frozen=True)
class VolumeGroup:
    paths: tuple[Path, ...]
    style: str = "single"
    name: str = "archive"

    @property
    def entry(self) -> Path:
        # PKZIP volumes use .zip as the final volume and entry point.
        return self.paths[-1] if self.style == "zip_split" else self.paths[0]


@dataclass
class Result:
    output: Path
    layers: int
    archives: int
    session: Path | None


Log = Callable[[str], None]
