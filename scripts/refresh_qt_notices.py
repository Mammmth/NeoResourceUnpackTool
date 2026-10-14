"""Refresh Qt attribution snapshots from checksum-pinned official source archives."""
from __future__ import annotations

import hashlib
import json
import posixpath
import ssl
import tarfile
import urllib.request
from pathlib import Path, PurePosixPath

import certifi

ROOT = Path(__file__).resolve().parents[1]


def render_notices(archive: Path, component: dict) -> str:
    # Read selected regular files only; never extract an upstream archive to disk.
    with tarfile.open(archive) as source:
        members = {m.name.split("/", 1)[1]: m for m in source.getmembers()
                   if m.isfile() and "/" in m.name}
        selected = {p for p in members if p.startswith("LICENSES/")}
        selected.update(p for p in members if p == "REUSE.toml" or
                        (p.startswith(("src/", "sources/")) and
                         PurePosixPath(p).name in {"qt_attribution.json", "REUSE.toml"}))
        def read(path):
            with source.extractfile(members[path]) as f:
                return f.read().decode("utf-8-sig")
        for path in sorted(selected.copy()):
            if not path.endswith("qt_attribution.json"):
                continue
            # Qt attribution files may contain literal line breaks in strings.
            entries = json.loads(read(path), strict=False)
            for entry in entries if isinstance(entries, list) else [entries]:
                files = entry.get("LicenseFile", [])
                for filename in files if isinstance(files, list) else [files]:
                    target = posixpath.normpath(posixpath.join(posixpath.dirname(path), filename))
                    if target not in members:
                        raise ValueError(f"Missing attribution license: {path}: {filename}")
                    selected.add(target)
        if not selected:
            raise ValueError(f"No license material in {archive}")
        header = (f"{component['name']} — upstream attribution snapshot\n"
                  f"Source: {component['url']}\nSHA-256: {component['sha256']}\n\n"
                  "This is a conservative source-module notice collection. Optional, test,\n"
                  "example or build-only components mentioned here are not necessarily bundled.\n"
                  "License alternatives remain alternatives; including a license text does not\n"
                  "grant a commercial license or relicense the application.\n")
        return header + "".join(f"\n{'=' * 72}\nUpstream file: {p}\n{'=' * 72}\n{read(p)}\n"
                                for p in sorted(selected))


def main():
    manifest = json.loads((ROOT / "third_party/qt-sources.json").read_text("utf-8"))
    cache = ROOT / "build/license-sources"
    cache.mkdir(parents=True, exist_ok=True)
    destination = ROOT / "third_party/qt"
    destination.mkdir(parents=True, exist_ok=True)
    for component in manifest["components"]:
        archive = cache / f"{component['name']}.tar.xz"
        if not archive.exists():
            context = ssl.create_default_context(cafile=certifi.where())
            with urllib.request.urlopen(component["url"], context=context, timeout=60) as response:
                archive.write_bytes(response.read())
        if hashlib.sha256(archive.read_bytes()).hexdigest() != component["sha256"]:
            raise ValueError(f"Source checksum mismatch: {archive}")
        text = render_notices(archive, component)
        (destination / f"{component['name']}-NOTICES.txt").write_text(text, encoding="utf-8")
        print(f"Collected {component['name']} notices ({len(text)} characters)")


if __name__ == "__main__":
    main()
