from __future__ import annotations

import json
import re
import shutil
import stat
import tempfile
import zipfile
from dataclasses import asdict
from pathlib import Path
from threading import Event
from typing import Callable

from .detect import OffsetReader, detect
from .engine import SevenZip
from .filename_encoding import normalize_zip_names
from .model import (
    ArchiveInfo, Cancelled, InvalidInput, Log, PasswordRequired, Result, Settings,
    TaskProgress, UnpackError, VolumeGroup,
)
from .volumes import discover_groups, is_volume_name, make_group
from .ziprepair import repair_zip_copy
from .ziprestore import join_zip_disks


def ignored(path: Path) -> bool:
    return path.name in {".DS_Store", "__MACOSX", "Thumbs.db", "desktop.ini"} or path.name.startswith("._")


def children(path: Path) -> list[Path]:
    return sorted((p for p in path.iterdir() if not ignored(p)), key=lambda p: p.name.casefold())


def available_name(directory: Path, name: str) -> Path:
    name = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", name).rstrip(" .") or "解压结果"
    existing = {p.name.casefold() for p in directory.iterdir()}
    candidate = directory / name
    index = 2
    while candidate.name.casefold() in existing:
        candidate = directory / f"{Path(name).stem} ({index}){Path(name).suffix}"
        index += 1
    return candidate


def content_container(path: Path, info: ArchiveInfo | None) -> bool:
    """Recognize final documents/apps that happen to use ZIP as their container."""
    markers = {
        ".docx": "word/document.xml", ".docm": "word/document.xml",
        ".xlsx": "xl/workbook.xml", ".xlsm": "xl/workbook.xml",
        ".pptx": "ppt/presentation.xml", ".pptm": "ppt/presentation.xml",
        ".epub": "META-INF/container.xml", ".apk": "AndroidManifest.xml",
        ".jar": "META-INF/MANIFEST.MF",
    }
    marker = markers.get(path.suffix.casefold())
    if not marker or not info or info.kind != "zip":
        return False
    try:
        with OffsetReader(path, info.offset) as stream, zipfile.ZipFile(stream) as archive:
            return marker in archive.namelist()
    except (zipfile.BadZipFile, OSError):
        return False


class UnpackTask:
    """One logical input, with cached successful nodes for password retry."""

    def __init__(self, paths: list[Path], settings: Settings, *, engine: Path | None = None,
                 log: Log = lambda _: None, cancel: Event | None = None, cache_root: Path | None = None,
                 progress: Callable[[TaskProgress], None] = lambda _: None):
        settings.validate()
        self.group = make_group(paths)
        self._output_name = self.group.name
        self.settings = settings
        self.log = log
        self.progress = progress
        self.current_archive = ""
        self.current_depth = 0
        self._stage = ""
        self.cancel = cancel or Event()
        self.engine = SevenZip(engine, self.cancel, self.extraction_progress)
        self.session = Path(tempfile.mkdtemp(prefix="neo-unpack-", dir=cache_root))
        self.nodes = self.session / "过程"
        self.nodes.mkdir()
        self.prepared: dict[tuple[str, ...], tuple[Path, ArchiveInfo, Path]] = {}
        self.extracted: dict[tuple[str, ...], Path] = {}
        self.detected: dict[tuple[str, bool], ArchiveInfo | None] = {}
        self.preferred_passwords: list[str] = []
        self.layers = 0
        self.archive_count = 0
        self._log_lines: list[str] = []
        self._finished = False
        self._filename_encoding = settings.filename_encoding
        self._input_stats = [(p, p.stat()) for p in self.group.paths]

    def emit(self, message: str) -> None:
        # Keep each event on one line, including user-provided names/passwords.
        message = message.replace("\r", "\\r").replace("\n", "\\n").replace("\t", "\\t")
        self._log_lines.append(message)
        self.log(message)

    def report_progress(self, stage: str, percent: int | None = None) -> None:
        if stage != self._stage:
            self._stage = stage
            self.emit(f"{stage}：{self.current_archive}" if self.current_archive else stage)
        self.progress(TaskProgress(stage, self.current_archive, self.current_depth, percent))

    def extraction_progress(self, percent: int | None) -> None:
        self.report_progress("检查压缩包" if percent is None else "解压", percent)

    def check_cancel(self) -> None:
        if self.cancel.is_set():
            raise Cancelled("已取消，原始文件与任务缓存已保留。")

    def copy(self, source: Path, target: Path, offset: int = 0) -> None:
        with source.open("rb") as original, target.open("wb") as output:
            original.seek(offset)
            while data := original.read(4 * 1024 * 1024):
                self.check_cancel()
                output.write(data)

    def info(self, group: VolumeGroup) -> ArchiveInfo | None:
        path = group.paths[0]
        split = group.style != "single"
        key = (str(path), split)
        if key not in self.detected:
            self.detected[key] = detect(path, split=split, cancel=self.cancel)
        return self.detected[key]

    def prepare(self, group: VolumeGroup) -> tuple[Path, ArchiveInfo, Path]:
        key = tuple(map(str, group.paths))
        if key in self.prepared:
            return self.prepared[key]
        self.check_cancel()
        self.report_progress("识别格式")
        info = self.info(group)
        if info is None:
            raise InvalidInput("没有识别到有效的 ZIP、RAR 或 7z 压缩结构，无法解压。")
        number = len(self.prepared) + 1
        safe_name = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", group.name)[:100].rstrip(" .") or "archive"
        directory = self.nodes / f"{number:03d}_{safe_name}"
        # A cancelled preparation may have left an incomplete directory.
        if directory.exists():
            shutil.rmtree(directory)
        directory.mkdir()
        source_dir = directory / "还原包"
        source_dir.mkdir()
        self.report_progress("合并分卷" if group.style == "numeric" else "复制与还原")
        if group.style == "numeric":
            if info.kind not in {"zip", "7z"}:
                raise InvalidInput("数字分卷首版支持 ZIP/7z；RAR 请使用标准 RAR 分卷命名。")
            archive = source_dir / f"archive.{info.kind}"
            with archive.open("wb") as output:
                for index, source in enumerate(group.paths):
                    with source.open("rb") as original:
                        original.seek(info.offset if index == 0 else 0)
                        while data := original.read(4 * 1024 * 1024):
                            self.check_cancel()
                            output.write(data)
        elif group.style != "single":
            expected = "zip" if group.style == "zip_split" else "rar"
            if info.kind != expected:
                raise InvalidInput("分卷命名与实际压缩格式不匹配。")
            staged_parts = []
            for index, source in enumerate(group.paths):
                if group.style == "rar_part":
                    staged_name = f"archive.part{index + 1:04d}.rar"
                elif group.style == "rar_old":
                    staged_name = "archive.rar" if index == 0 else f"archive.r{index - 1:02d}"
                else:
                    staged_name = source.name
                target = source_dir / staged_name
                self.copy(source, target, info.offset if index == 0 else 0)
                staged_parts.append(target)
            archive = staged_parts[-1] if group.style == "zip_split" else staged_parts[0]
            if group.style == "zip_split":
                self.report_progress("合并分卷")
                archive = directory / "合并包.zip"
                join_zip_disks(staged_parts, archive, self.cancel)
        else:
            archive = source_dir / f"archive.{info.kind}"
            self.copy(group.paths[0], archive, info.offset)
        if info.offset:
            self.emit(f"检测到前置载体，已从偏移 {info.offset:,} 处还原 {info.kind.upper()}。")
        else:
            self.emit(f"已识别 {info.kind.upper()}，使用副本处理。")
        if info.kind == "zip":
            self.report_progress("检查 ZIP 伪加密")
            repaired = directory / "修复包.zip"
            self.copy(archive, repaired)
            count = repair_zip_copy(repaired, self.cancel)
            if count:
                archive = repaired
                self.emit(f"已校验并修复 {count} 个 ZIP 伪加密条目。")
            else:
                repaired.unlink()
            named = directory / "文件名还原包.zip"
            self.report_progress("处理文件名编码")
            if normalize_zip_names(archive, named, self.settings.filename_encoding, self.cancel, self.emit):
                archive = named
        self.prepared[key] = archive, info, directory
        self.save_manifest("处理中")
        return self.prepared[key]

    def unpack(self, group: VolumeGroup) -> Path:
        key = tuple(map(str, group.paths))
        if key in self.extracted:
            self.emit("复用已成功解压的层，继续处理。")
            return self.extracted[key]
        archive, _, directory = self.prepare(group)
        destination = directory / "解压结果"
        candidates = [None, *dict.fromkeys(p for p in [*self.preferred_passwords, *self.settings.passwords] if p)]
        needed_password = False
        for index, password in enumerate(candidates):
            self.check_cancel()
            if destination.exists():
                shutil.rmtree(destination)
            if password is not None:
                self.emit(f"正在尝试密码 {index}/{len(candidates) - 1}…")
            try:
                self.engine.extract(archive, destination, password)
            except PasswordRequired:
                needed_password = True
                continue
            self.extracted[key] = destination
            self.archive_count += 1
            if password is not None:
                self.preferred_passwords = [password, *[p for p in self.preferred_passwords if p != password]]
                self.emit(f"密码（{password}）验证成功，后续层将优先尝试。")
            self.save_manifest("处理中")
            return destination
        if destination.exists():
            shutil.rmtree(destination)
        if needed_password:
            message = "当前密码库未能解开此压缩包。请补充正确密码后继续；加密数据损坏也可能导致相同错误。"
            if len(candidates) == 1:
                message = "此压缩包需要密码，当前密码库为空。请添加密码后继续。"
            raise PasswordRequired(message)
        raise UnpackError("解压未完成。")

    def _walk(self, group: VolumeGroup, depth: int, finals: list[tuple[Path, str]]) -> str:
        """Collect results and return the innermost name shared by all branches."""
        self.check_cancel()
        if depth > 64 or self.archive_count >= 10000:
            raise UnpackError("递归层数或包数量异常，已停止并保留缓存。")
        self.layers = max(self.layers, depth)
        self.current_archive, self.current_depth = group.entry.name, depth
        self.emit(f"正在处理第 {depth} 层：{group.entry.name}")
        self.report_progress("准备处理")
        extracted = self.unpack(group)
        self.report_progress("检查本层内容")
        root = extracted
        # Peel wrapper-only directories. Sidecar TXT files do not add layers.
        while True:
            items = children(root)
            dirs = [p for p in items if p.is_dir()]
            files = [p for p in items if p.is_file()]
            if len(dirs) == 1 and all(p.suffix.casefold() == ".txt" for p in files):
                finals.extend((p, group.name) for p in files)
                root = dirs[0]
            else:
                break
        items = children(root)
        if self.settings.depth == "one":
            finals.extend((p, group.name) for p in items)
            return group.name
        # Directories alongside content are part of that content; don't flatten them.
        if any(p.is_dir() for p in items):
            finals.extend((p, group.name) for p in items)
            return group.name
        texts = [p for p in items if p.suffix.casefold() == ".txt"]
        nontexts = [p for p in items if p.suffix.casefold() != ".txt"]
        for path in texts[:]:
            if self.info(VolumeGroup((path,), name=path.stem)):
                texts.remove(path)
                nontexts.append(path)
        # A single renamed archive can have any extension, including .txt.
        if len(items) == 1:
            nontexts, texts = items, []
        if not nontexts:
            finals.extend((p, group.name) for p in texts)
            return group.name
        for path in nontexts:
            if is_volume_name(path):
                continue
            if path.suffix.casefold() == ".zip" and any(re.fullmatch(re.escape(path.stem) + r"\.z\d{2,}", other.name, re.I) for other in nontexts):
                continue
            info = self.info(VolumeGroup((path,), name=path.stem))
            if info is None or content_container(path, info):
                finals.extend((p, group.name) for p in items)
                return group.name
        try:
            groups = discover_groups(nontexts)
            archive_infos = [self.info(candidate) for candidate in groups]
        except InvalidInput:
            finals.extend((p, group.name) for p in items)
            return group.name
        if any(info is None for info in archive_infos):
            finals.extend((p, group.name) for p in items)
            return group.name
        finals.extend((p, group.name) for p in texts)
        names = [self._walk(candidate, depth + 1, finals) for candidate in groups]
        return names[0] if len(names) == 1 else group.name

    def _copy_final(self, source: Path, target: Path) -> None:
        self.check_cancel()
        if source.is_dir():
            target.mkdir()
            for child in children(source):
                self._copy_final(child, target / child.name)
        else:
            self.copy(source, target)
            shutil.copystat(source, target)

    def _publish(self, finals: list[tuple[Path, str]]) -> Path:
        mode = self.settings.output_mode
        base = Path(self.settings.fixed_directory).expanduser().absolute() if mode.startswith("fixed") else self.group.paths[0].parent
        base.mkdir(parents=True, exist_ok=True)
        new_folder = mode in {"folder", "fixed_folder"}
        name = self._output_name if mode == "fixed_folder" else self.group.name
        output = available_name(base, name) if new_folder else base
        if new_folder:
            output.mkdir()
        staged = Path(tempfile.mkdtemp(prefix=".neo-publish-", dir=base))
        moved: list[Path] = []
        try:
            # Separate branch bundles when a collision would otherwise rename an
            # application's resource directory independently of its executable.
            bundles: dict[tuple[Path, str], list[Path]] = {}
            for path, name in finals:
                bundles.setdefault((path.parent, name), []).append(path)
            existing = {p.name.casefold() for p in output.iterdir()}
            for (_, name), paths in bundles.items():
                self.check_cancel()
                collision = any(p.name.casefold() in existing for p in paths)
                if collision and any(p.is_dir() for p in paths):
                    target_dir = available_name(staged, name)
                    while target_dir.name.casefold() in existing:
                        target_dir = target_dir.with_name(target_dir.name + "_结果")
                    target_dir.mkdir()
                    for path in paths:
                        self._copy_final(path, target_dir / path.name)
                    existing.add(target_dir.name.casefold())
                else:
                    for path in paths:
                        wanted = path.name
                        if wanted.casefold() in existing:
                            wanted = f"{path.stem}_{name}{path.suffix}"
                        target = available_name(staged, wanted)
                        while target.name.casefold() in existing:
                            target = target.with_name(f"{target.stem}_2{target.suffix}")
                        self._copy_final(path, target)
                        existing.add(target.name.casefold())
            if self.settings.keep_intermediates:
                target = available_name(staged, "_中间产物")
                while target.name.casefold() in existing:
                    target = target.with_name(target.name + "_2")
                self._copy_final(self.nodes, target)
            self.check_cancel()
            for path in staged.iterdir():
                destination = output / path.name
                # The process never overwrites a pre-existing destination.
                if destination.exists():
                    raise UnpackError("输出目录在整理期间发生变化，请重试。已有文件未覆盖。")
                path.rename(destination)
                moved.append(destination)
            return output
        except BaseException:
            for destination in reversed(moved):
                destination.rename(staged / destination.name)
            if new_folder and output.exists() and not any(output.iterdir()):
                output.rmdir()
            raise
        finally:
            shutil.rmtree(staged, ignore_errors=True)

    def save_manifest(self, status: str) -> None:
        payload = {
            "status": status, "inputs": [str(p) for p in self.group.paths],
            "completed": {" | ".join(k): str(v) for k, v in self.extracted.items()},
            "settings": {k: v for k, v in asdict(self.settings).items() if k != "passwords"},
        }
        (self.session / "task.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2), "utf-8")
        (self.session / "process.log").write_text("\n".join(self._log_lines), "utf-8")

    @staticmethod
    def _same_source(path: Path, original) -> bool:
        current = path.lstat()
        return stat.S_ISREG(current.st_mode) and all(
            getattr(current, field) == getattr(original, field)
            for field in ("st_dev", "st_ino", "st_size", "st_mtime_ns", "st_ctime_ns")
        )

    def _delete_sources(self) -> tuple[int, list[str]]:
        # Called only after publishing results and writing the success manifest.
        # Preflight every selected volume before unlinking any of them.
        self.report_progress("删除原文件")
        if self.cancel.is_set():
            message = "解压已完成，已取消原文件删除，原文件保留。"
            self.emit(message)
            return 0, [message]
        for path, original in self._input_stats:
            try:
                unchanged = self._same_source(path, original)
            except OSError:
                unchanged = False
            if not unchanged:
                message = f"解压已完成，原文件发生变化或无法检查，未删除本组原文件：{path}"
                self.emit(message)
                return 0, [message]
        if self.cancel.is_set():
            message = "解压已完成，已取消原文件删除，原文件保留。"
            self.emit(message)
            return 0, [message]
        deleted, warnings = 0, []
        # Results are committed. Finish this short cleanup without cancelling
        # between volumes; unlink failures are warnings, never extraction retries.
        for path, original in self._input_stats:
            try:
                if not self._same_source(path, original):
                    raise OSError("原文件已发生变化")
                path.unlink()
            except OSError:
                message = f"解压已完成，但原文件未删除（可能已变化、被占用或无权限）：{path}"
                warnings.append(message)
                self.emit(message)
            else:
                deleted += 1
                self.emit(f"已删除原文件：{path}")
        return deleted, warnings

    def run(self) -> Result:
        if self._finished:
            raise InvalidInput("此任务已完成，请新建任务。")
        self.settings.validate()
        try:
            for path, original in self._input_stats:
                if not self._same_source(path, original):
                    raise InvalidInput("原始输入在处理期间发生变化，请重新选择文件。")
            if self.settings.filename_encoding != self._filename_encoding:
                # Successful cached layers may have decoded names using the old
                # setting. Start a new tree while preserving failed-run evidence.
                self.nodes = Path(tempfile.mkdtemp(prefix="过程_", dir=self.session))
                self.prepared.clear()
                self.extracted.clear()
                self.detected.clear()
                self.layers = self.archive_count = 0
                self._filename_encoding = self.settings.filename_encoding
                self.emit("文件名编码已更改，重新从原始输入处理；旧过程缓存已保留。")
            finals: list[tuple[Path, str]] = []
            self._output_name = self._walk(self.group, 1, finals)
            self.emit("解压完成，正在整理最终内容…")
            self.current_archive, self.current_depth = "", 0
            self.report_progress("整理结果")
            output = self._publish(finals)
            self.check_cancel()
            self.emit(f"完成，共处理 {self.archive_count} 个压缩包、{self.layers} 层。")
            self.save_manifest("完成")
        except Exception as error:
            self.save_manifest(type(error).__name__)
            raise
        self._finished = True
        deleted, warnings = self._delete_sources() if self.settings.delete_sources else (0, [])
        if self.settings.delete_sources:
            try:
                self.save_manifest("解压完成，原文件清理未完成" if warnings else "完成")
            except OSError:
                message = "解压已完成，无法保存原文件清理记录。"
                warnings.append(message)
                self.emit(message)
        if not warnings:
            shutil.rmtree(self.session, ignore_errors=True)
        return Result(output, self.layers, self.archive_count, self.session if warnings else None, deleted, warnings)
