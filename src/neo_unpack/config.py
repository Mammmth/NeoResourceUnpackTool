from __future__ import annotations

import json
import os
import sys
from dataclasses import asdict, fields
from pathlib import Path

from .model import Settings


def config_directory() -> Path:
    if sys.platform == "win32":
        root = Path(os.environ.get("APPDATA", Path.home() / "AppData/Roaming"))
    elif sys.platform == "darwin":
        root = Path.home() / "Library/Application Support"
    else:
        root = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config"))
    return root / "NeoResourceUnpackTool"


def load_settings(directory: Path | None = None) -> Settings:
    path = (directory or config_directory()) / "settings.json"
    if not path.exists():
        return Settings()
    data = json.loads(path.read_text("utf-8"))
    settings = Settings(**{f.name: data[f.name] for f in fields(Settings) if f.name in data})
    if not isinstance(settings.passwords, list) or not all(isinstance(p, str) for p in settings.passwords):
        raise ValueError("密码库配置格式不正确。")
    settings.validate()
    return settings


def save_settings(settings: Settings, directory: Path | None = None) -> None:
    settings.validate()
    directory = directory or config_directory()
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / "settings.json"
    temporary = directory / "settings.json.tmp"
    with temporary.open("w", encoding="utf-8") as stream:
        if os.name != "nt":
            os.chmod(temporary, 0o600)
        json.dump(asdict(settings), stream, ensure_ascii=False, indent=2)
    temporary.replace(path)
