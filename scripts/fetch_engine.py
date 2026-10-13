"""Fetch a pinned official 7-Zip engine. Does not install 7-Zip system-wide."""
from __future__ import annotations

import argparse
import hashlib
import os
import shutil
import ssl
import subprocess
import sys
import tarfile
import tempfile
import urllib.request
from pathlib import Path

VERSION = "26.04"
PACKAGES = {
    "macos": ("7z2604-mac.tar.xz", "bee04358cbcbc7106273cee0e8d72916db2696c48067a3538c34d8cd6fd16578"),
    "windows": ("7z2604-x64.msi", "0b01334a418654293513449f61d6bfc99e5196ca371e3c3dc961eda57cd535c6"),
}
ROOT = Path(__file__).resolve().parents[1]


def main():
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--platform", choices=PACKAGES, default="windows" if sys.platform == "win32" else "macos")
    parser.add_argument("--no-proxy", action="store_true", help="仅本次下载不使用代理")
    parser.add_argument("--archive", type=Path, help="使用已经下载的官方安装包（仍校验 SHA-256）")
    args = parser.parse_args()
    filename, checksum = PACKAGES[args.platform]
    destination = ROOT / "vendor/7zip" / args.platform
    with tempfile.TemporaryDirectory(prefix="neo-engine-") as tmp:
        temp = Path(tmp)
        package = args.archive or temp / filename
        if not args.archive:
            try:
                import certifi
                context = ssl.create_default_context(cafile=certifi.where())
            except ImportError:
                context = ssl.create_default_context()
            handlers = [urllib.request.HTTPSHandler(context=context)]
            if args.no_proxy:
                handlers.append(urllib.request.ProxyHandler({}))
            opener = urllib.request.build_opener(*handlers)
            urls = [f"https://www.7-zip.org/a/{filename}", f"https://github.com/ip7z/7zip/releases/download/{VERSION}/{filename}"]
            for index, url in enumerate(urls):
                try:
                    with opener.open(url, timeout=45) as source, package.open("wb") as output:
                        shutil.copyfileobj(source, output)
                    break
                except (OSError, urllib.error.URLError):
                    if index == len(urls) - 1:
                        raise
        actual = hashlib.sha256(package.read_bytes()).hexdigest()
        if actual != checksum:
            raise SystemExit("官方安装包 SHA-256 不匹配，已停止。")
        unpacked = temp / "unpacked"
        unpacked.mkdir()
        if args.platform == "macos":
            with tarfile.open(package) as archive:
                archive.extractall(unpacked, filter="data")
            sources = {"7zz": unpacked / "7zz", "License.txt": unpacked / "License.txt"}
        else:
            existing = shutil.which("7z")
            candidates = [Path(os.environ.get("ProgramFiles", "C:/Program Files")) / "7-Zip/7z.exe", ROOT / "vendor/7zip/macos/7zz"]
            bootstrap = Path(existing) if existing else next((p for p in candidates if p.is_file()), None)
            if bootstrap:
                subprocess.run([str(bootstrap), "x", "-y", f"-o{unpacked}", str(package), "_7z.exe", "_7z.dll", "License.txt"], check=True)
                sources = {name: unpacked / ("_" + name if name != "License.txt" else name) for name in ("7z.exe", "7z.dll", "License.txt")}
            elif sys.platform == "win32":
                # MSI administrative extraction, not a system installation.
                subprocess.run(["msiexec", "/a", str(package.resolve()), "/qn", f"TARGETDIR={unpacked.resolve()}"], check=True)
                sources = {name: next(unpacked.rglob(name)) for name in ("7z.exe", "7z.dll", "License.txt")}
            else:
                raise SystemExit("请先准备本机 7-Zip 引擎，或在 Windows 上运行此脚本。")
        destination.mkdir(parents=True, exist_ok=True)
        for name, source in sources.items():
            if not source.is_file():
                raise SystemExit(f"官方包缺少必要文件：{name}")
            shutil.copy2(source, destination / name)
        if args.platform == "macos":
            (destination / "7zz").chmod(0o755)
        print(f"7-Zip {VERSION} 已准备：{destination}")


if __name__ == "__main__":
    main()
