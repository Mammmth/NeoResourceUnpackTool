"""Assemble readable license materials and exact-version source information."""
from __future__ import annotations

import hashlib
import importlib.metadata
import json
import platform
import re
import shutil
import ssl
import subprocess
import sys
import urllib.request
from pathlib import Path

import certifi

ROOT = Path(__file__).resolve().parents[1]
PACKAGES = ("PySide6-Essentials", "shiboken6", "pyinstaller", "pyinstaller-hooks-contrib",
            "pyzipper", "pycryptodomex", "charset-normalizer")


def download(url: str) -> bytes:
    context = ssl.create_default_context(cafile=certifi.where())
    with urllib.request.urlopen(url, context=context, timeout=45) as response:
        return response.read()


def copy_package_licenses(distribution, destination: Path) -> list[str]:
    copied = []
    for file in distribution.files or []:
        if "licenses" in file.parts:
            relative = Path(*file.parts[file.parts.index("licenses") + 1:])
        elif file.name.upper().startswith(("LICENSE", "COPYING", "NOTICE", "AUTHORS")):
            relative = Path(file.name)
        else:
            continue
        target = destination / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(distribution.locate_file(file), target)
        copied.append(relative.as_posix())
    return copied


def collect(host: str) -> Path:
    from PySide6.QtCore import qVersion

    qt_sources = json.loads((ROOT / "third_party/qt-sources.json").read_text("utf-8"))
    for name in ("PySide6-Essentials", "shiboken6"):
        if importlib.metadata.version(name) != qt_sources["version"]:
            raise RuntimeError(f"{name} 版本与 Qt 许可快照不一致，请更新 third_party/qt-sources.json 和声明")
    if qVersion() != qt_sources["version"]:
        raise RuntimeError("Qt 运行库版本与许可快照不一致")
    destination = ROOT / "build/legal"
    if destination.exists():
        shutil.rmtree(destination)
    shutil.copytree(ROOT / "third_party", destination / "third_party")
    for name in ("LICENSE", "THIRD_PARTY_NOTICES.md", "SOURCE_CODE.md"):
        shutil.copyfile(ROOT / name, destination / name)
    # Use the notice from the actual checked official platform package.
    shutil.copyfile(ROOT / "vendor/7zip" / host / "License.txt",
                    destination / "third_party/7zip-License.txt")
    for component in qt_sources["components"]:
        notice = destination / "third_party/qt" / f"{component['name']}-NOTICES.txt"
        if not notice.is_file() or component["sha256"] not in notice.read_text("utf-8"):
            raise RuntimeError(f"缺少或过期的 Qt 许可快照：{notice.name}")

    packages = []
    for name in PACKAGES:
        distribution = importlib.metadata.distribution(name)
        files = copy_package_licenses(distribution, destination / "third_party/package-licenses" / name)
        item = {"name": name, "version": distribution.version, "license_files": files}
        if name in ("PySide6-Essentials", "shiboken6"):
            item["source"] = qt_sources["components"][2]["url"]
        else:
            if not files:
                raise RuntimeError(f"未找到 {name} 的许可证，停止构建")
            metadata = json.loads(download(f"https://pypi.org/pypi/{name}/{distribution.version}/json"))
            sources = [f for f in metadata["urls"] if f["packagetype"] == "sdist"]
            if not sources:
                raise RuntimeError(f"未找到 {name} 的对应版本源码")
            item["source"] = sources[0]["url"]
            item["source_sha256"] = sources[0]["digests"]["sha256"]
        packages.append(item)

    python_version = platform.python_version()
    openssl_match = re.match(r"OpenSSL (3\.\d+\.\d+)\b", ssl.OPENSSL_VERSION)
    if not openssl_match:
        raise RuntimeError("请先核对当前 OpenSSL 的许可证与源码版本")
    openssl_version = openssl_match.group(1)
    runtime = [
        {"name": "Python", "version": python_version,
         "license_url": f"https://raw.githubusercontent.com/python/cpython/v{python_version}/LICENSE",
         "notices_url": f"https://raw.githubusercontent.com/python/cpython/v{python_version}/Doc/license.rst",
         "source": f"https://www.python.org/ftp/python/{python_version}/Python-{python_version}.tar.xz"},
        {"name": "OpenSSL", "version": openssl_version,
         "license_url": f"https://raw.githubusercontent.com/openssl/openssl/openssl-{openssl_version}/LICENSE.txt",
         "source": f"https://github.com/openssl/openssl/releases/download/openssl-{openssl_version}/openssl-{openssl_version}.tar.gz"},
    ]
    for item in runtime:
        data = download(item["license_url"])
        if b"License" not in data and b"LICENSE" not in data:
            raise RuntimeError(f"无效的许可文件：{item['name']}")
        relative = f"third_party/{item['name']}-LICENSE.txt"
        (destination / relative).write_bytes(data)
        item["license_file"] = relative
        item["license_sha256"] = hashlib.sha256(data).hexdigest()
        if "notices_url" in item:
            notices = download(item["notices_url"])
            if b"Licenses and Acknowledgements" not in notices:
                raise RuntimeError("Python 第三方声明格式已改变，请重新核对")
            relative = "third_party/Python-NOTICES.txt"
            (destination / relative).write_bytes(notices)
            item["notices_file"] = relative
            item["notices_sha256"] = hashlib.sha256(notices).hexdigest()

    def git(*args):
        return subprocess.check_output(["git", *args], cwd=ROOT, text=True).strip()
    commit = git("rev-parse", "HEAD")
    info = {"application_license": "GPL-3.0-only", "copyright": "Copyright (c) 2026 Mammmth",
            "commit": commit, "working_tree_modified": bool(git("status", "--porcelain")),
            "application_source": f"https://github.com/Mammmth/NeoResourceUnpackTool/archive/{commit}.zip",
            "platform": host, "architecture": platform.machine(), "qt": qt_sources,
            "packages": packages, "runtime": runtime,
            "7zip_source": "https://www.7-zip.org/a/7z2604-src.tar.xz"}
    (destination / "BUILD_INFO.json").write_text(json.dumps(info, ensure_ascii=False, indent=2) + "\n", "utf-8")
    # Record all distributed legal files, so the packaged smoke test can detect omissions.
    hashes = {p.relative_to(destination).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
              for p in sorted(destination.rglob("*")) if p.is_file()}
    (destination / "FILES.sha256.json").write_text(json.dumps(hashes, indent=2) + "\n", "utf-8")
    return destination


if __name__ == "__main__":
    print(collect("windows" if sys.platform == "win32" else "macos"))
