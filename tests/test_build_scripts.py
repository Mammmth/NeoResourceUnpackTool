import os
import subprocess
import sys
from pathlib import Path


def test_fetch_help_under_legacy_windows_encoding():
    root = Path(__file__).resolve().parents[1]
    environment = os.environ.copy()
    environment["PYTHONIOENCODING"] = "cp1252"
    result = subprocess.run([sys.executable, str(root / "scripts/fetch_engine.py"), "--help"],
                            env=environment, capture_output=True, timeout=15)
    assert result.returncode == 0
    assert "下载" in result.stdout.decode("utf-8")
