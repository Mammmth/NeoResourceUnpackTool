from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
import tempfile
import uuid
from pathlib import Path, PurePosixPath
from threading import Event
from typing import Callable

from .model import Cancelled, MissingVolume, PasswordRequired, UnpackError, UnsafeArchive
from .filename_encoding import zip_member_name


def find_engine() -> Path:
    executable = "7z.exe" if sys.platform == "win32" else "7zz"
    platform_name = "windows" if sys.platform == "win32" else "macos" if sys.platform == "darwin" else "linux"
    roots = [Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parents[2])), Path(__file__).resolve().parents[2]]
    for root in roots:
        candidate = root / "vendor" / "7zip" / platform_name / executable
        if candidate.is_file():
            return candidate
    configured = os.environ.get("NEO_7ZIP")
    if configured and Path(configured).is_file():
        return Path(configured).absolute()
    for name in (executable, "7zz", "7z"):
        if found := shutil.which(name):
            return Path(found)
    raise UnpackError("找不到完整 7-Zip 引擎。开发环境请运行 scripts/fetch_engine.py；发行版请重新安装。")


def check_member_path(name: str) -> None:
    normalized = name.replace("\\", "/")
    path = PurePosixPath(normalized)
    if not name or path.is_absolute() or ".." in path.parts or ":" in normalized or "\x00" in name:
        raise UnsafeArchive("压缩包包含越界路径，已停止解压。")
    # Windows devices are unsafe even when a test runs on macOS.
    for part in path.parts:
        if part.endswith((" ", ".")) or re.match(r"^(CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])(?:\.|$)", part, re.I):
            raise UnsafeArchive("压缩包包含不兼容的文件名，已停止解压。")


class SevenZip:
    def __init__(self, executable: Path | None = None, cancel: Event | None = None,
                 progress: Callable[[int | None], None] = lambda _: None):
        self.executable = executable or find_engine()
        self.cancel = cancel or Event()
        self.progress = progress
        # Bare -p asks for interactive input, including in a detached Windows
        # process. An explicit throwaway probe keeps encrypted-header detection
        # non-interactive; actual passwords still come only from the user's list.
        self._probe = "neo-probe-" + uuid.uuid4().hex

    def run(self, arguments: list[str], password: str | None = None, *, track_progress=False) -> tuple[int, str]:
        if self.cancel.is_set():
            raise Cancelled("已取消。")
        # No shell. Never log command arguments or raw engine output.
        command = [str(self.executable), *arguments]
        environment = os.environ.copy()
        environment["LC_ALL"] = "C"
        with tempfile.TemporaryFile() as output, tempfile.TemporaryDirectory(prefix="neo-progress-") as folder:
            # Separate handles keep reader seeks from moving the child's write offset.
            progress_path = Path(folder) / "progress"
            with progress_path.open("wb") as progress_output, progress_path.open("rb") as progress_input:
                return self._run_process(command, environment, output, progress_output, progress_input, track_progress)

    def _run_process(self, command, environment, output, progress_output, progress_input, track_progress):
        pending = b""
        last_percent = -1

        def read_progress():
            nonlocal pending, last_percent
            if not track_progress:
                return
            while chunk := progress_input.read(8192):
                pending += chunk
                # 7-Zip uses carriage returns / backspaces, not necessarily lines.
                segments = re.split(rb"[\r\n\b]", pending)
                for segment in segments:
                    match = re.match(rb" *([0-9]{1,3})%", segment)
                    if match and int(match[1]) <= 100:
                        percent = min(99, int(match[1]))
                        if percent > last_percent:
                            last_percent = percent
                            self.progress(percent)
                # Bound memory even for very long progress filenames.
                pending = segments[-1]
                if len(pending) > 4096:
                    pending = b"x"

        try:
            process = subprocess.Popen(
                command, stdin=subprocess.DEVNULL, stdout=output,
                stderr=progress_output if track_progress else subprocess.STDOUT,
                env=environment,
                creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
            )
        except OSError as error:
            raise UnpackError("无法启动内置 7-Zip 引擎，请检查安装文件和执行权限。") from error
        try:
            while process.poll() is None:
                read_progress()
                if self.cancel.wait(0.1):
                    process.terminate()
                    try:
                        process.wait(timeout=3)
                    except subprocess.TimeoutExpired:
                        process.kill()
                        process.wait()
                    raise Cancelled("已取消，原始输入和任务缓存已保留。")
            read_progress()
            output.seek(0)
            text = output.read().decode("utf-8", errors="replace")
        finally:
            if process.poll() is None:
                process.kill()
                process.wait()
        return process.returncode, text

    @staticmethod
    def _failure(text: str, *, password: str | None, encrypted: bool = False) -> None:
        lower = text.casefold()
        if any(message in lower for message in ("missing volume", "unexpected end of archive", "cannot find volume", "can't open as archive: 1")):
            raise MissingVolume("分卷缺失或压缩包被截断。请确认已拖入全部分卷且下载完整。")
        if "wrong password" in lower or "password is incorrect" in lower or "can not open encrypted archive" in lower:
            raise PasswordRequired("需要正确密码，或加密数据已经损坏。")
        if encrypted and "data error" in lower:
            raise PasswordRequired("需要正确密码，或加密数据已经损坏。")
        if "unsupported method" in lower:
            raise UnpackError("压缩包使用了当前引擎不支持的压缩或加密方法。")
        if "not enough space" in lower or "no space left" in lower:
            raise UnpackError("磁盘剩余空间不足。任务缓存已保留。")
        if "access is denied" in lower or "permission denied" in lower:
            raise UnpackError("没有写入目标位置的权限。")
        raise UnpackError("压缩包结构或数据校验失败，可能已损坏；这不一定是密码错误。")

    def inspect(self, archive: Path, password: str | None = None) -> list[dict[str, str]]:
        code, text = self.run(["l", "-slt", "-sccUTF-8", "-p" + (password if password is not None else self._probe), "--", str(archive)], password)
        if code:
            self._failure(text, password=password)
        # Keep the archive header for errors (e.g. a missing last RAR volume),
        # but parse only member records after the separator.
        _, separator, text = text.replace("\r\n", "\n").partition("\n----------\n")
        if not separator:
            raise UnpackError("无法解析压缩包目录，未执行解压。")
        records: list[dict[str, str]] = []
        for block in re.split(r"\r?\n\s*\r?\n", text):
            record = {}
            for line in block.splitlines():
                if " = " in line:
                    key, value = line.split(" = ", 1)
                    record[key] = value
            if "Path" not in record:
                continue
            check_member_path(record["Path"])
            attributes = record.get("Attributes", "")
            if record.get("Symbolic Link") or record.get("Hard Link") or re.search(r"(?:^| )l[rwx-]{9}", attributes):
                raise UnsafeArchive("压缩包包含符号链接或硬链接，首版不解压此类条目。")
            records.append(record)
        return records

    def extract(self, archive: Path, destination: Path, password: str | None = None) -> None:
        self.progress(None)
        records = self.inspect(archive, password)
        encrypted = any(record.get("Encrypted") == "+" for record in records)
        if encrypted and password is None:
            raise PasswordRequired("此压缩包需要密码。")
        destination.mkdir(parents=True, exist_ok=False)
        self.progress(0)
        code, text = self.run([
            "x", "-y", "-aou", "-bsp2", "-bse1", "-bso1", "-bb0", "-sccUTF-8", "-p" + (password if password is not None else self._probe),
            "-o" + str(destination), "--", str(archive),
        ], password, track_progress=True)
        if code:
            try:
                self._failure(text, password=password, encrypted=encrypted)
            except PasswordRequired:
                if password and not password.isascii() and archive.suffix.lower() == ".zip":
                    self._extract_utf8_zip(archive, destination, password)
                else:
                    raise
        for path in destination.rglob("*"):
            if self.cancel.is_set():
                raise Cancelled("已取消，任务缓存已保留。")
            if path.is_symlink() or (path.is_file() and path.stat().st_nlink > 1):
                raise UnsafeArchive("解压结果包含链接，任务已停止，未写入最终目录。")
        self.progress(100)

    def _extract_utf8_zip(self, archive: Path, destination: Path, password: str) -> None:
        # Windows 7-Zip converts ZIP passwords with CP_ACP, even for ZIPs made
        # with UTF-8 passwords. Retry the same user password as UTF-8 bytes.
        import pyzipper

        shutil.rmtree(destination)
        destination.mkdir()
        self.progress(0)
        completed = 0
        last_percent = 0
        try:
            with pyzipper.AESZipFile(archive) as source:
                source.setpassword(password.encode("utf-8"))
                members = source.infolist()
                total = sum(member.file_size for member in members if not member.is_dir())
                names = [zip_member_name(member) for member in members]
                for member, name in zip(members, names):
                    check_member_path(name)
                    if (member.external_attr >> 16) & 0o170000 == 0o120000:
                        raise UnsafeArchive("压缩包包含符号链接，已停止解压。")
                for member, name in zip(members, names):
                    if self.cancel.is_set():
                        raise Cancelled("已取消，任务缓存已保留。")
                    target = destination.joinpath(*PurePosixPath(name.replace("\\", "/")).parts)
                    if member.is_dir():
                        target.mkdir(parents=True, exist_ok=True)
                        continue
                    target.parent.mkdir(parents=True, exist_ok=True)
                    original = target
                    index = 1
                    while target.exists():
                        target = original.with_name(f"{original.stem}_{index}{original.suffix}")
                        index += 1
                    with source.open(member) as reader, target.open("xb") as writer:
                        while block := reader.read(1024 * 1024):
                            if self.cancel.is_set():
                                raise Cancelled("已取消，任务缓存已保留。")
                            writer.write(block)
                            completed += len(block)
                            percent = min(99, completed * 100 // max(1, total))
                            if percent != last_percent:
                                last_percent = percent
                                self.progress(percent)
        except (RuntimeError, pyzipper.BadZipFile, NotImplementedError) as error:
            raise PasswordRequired("需要正确密码，或加密数据已经损坏。") from error
