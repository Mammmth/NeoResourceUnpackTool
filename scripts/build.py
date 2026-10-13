from __future__ import annotations

import importlib.metadata
import platform
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
NAME = "NeoResourceUnpackTool"


def main():
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")
    if sys.platform not in {"win32", "darwin"}:
        raise SystemExit("请分别在 Windows 和 macOS 上构建。")
    host = "windows" if sys.platform == "win32" else "macos"
    engine_dir = ROOT / "vendor/7zip" / host
    engine = engine_dir / ("7z.exe" if host == "windows" else "7zz")
    if not engine.exists():
        raise SystemExit("请先运行 python scripts/fetch_engine.py")
    licenses = ROOT / "build" / "licenses"
    licenses.mkdir(parents=True, exist_ok=True)
    for package in ["PySide6-Essentials", "shiboken6", "pyinstaller", "pyzipper", "pycryptodomex", "charset-normalizer"]:
        distribution = importlib.metadata.distribution(package)
        for file in distribution.files or []:
            if "licenses" in file.parts:
                target = licenses / package / Path(*file.parts[file.parts.index("licenses") + 1:])
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(distribution.locate_file(file), target)
            elif file.name.upper().startswith(("LICENSE", "COPYING")):
                target = licenses / package / file.name
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(distribution.locate_file(file), target)
    args = [sys.executable, "-m", "PyInstaller", "--noconfirm", "--clean", "--windowed", "--name", NAME,
            "--paths", str(ROOT / "src"), "--distpath", str(ROOT / "dist"), "--workpath", str(ROOT / "build/pyinstaller"),
            "--specpath", str(ROOT / "build"), "--add-data", f"{ROOT / 'third_party'}:third_party",
            "--add-data", f"{licenses}:third_party/package-licenses", "--add-data", f"{ROOT / 'THIRD_PARTY_NOTICES.md'}:.",
            "--add-binary", f"{engine}:vendor/7zip/{host}"]
    if host == "windows":
        args.extend(["--onefile", "--add-binary", f"{engine_dir / '7z.dll'}:vendor/7zip/windows"])
    else:
        args.extend(["--onedir", "--osx-bundle-identifier", "com.neo.resourceunpacktool"])
    args.append(str(ROOT / "scripts/desktop_entry.py"))
    subprocess.run(args, cwd=ROOT, check=True)
    if host == "macos":
        destination = ROOT / "dist" / f"{NAME}-macos-{platform.machine()}.zip"
        subprocess.run(["ditto", "-c", "-k", "--sequesterRsrc", "--keepParent", str(ROOT / "dist" / f"{NAME}.app"), str(destination)], check=True)
    print("构建完成：", ROOT / "dist")


if __name__ == "__main__":
    main()
