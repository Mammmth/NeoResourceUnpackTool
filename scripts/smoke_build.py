import json
import os
import subprocess
import sys
from pathlib import Path

for stream in (sys.stdout, sys.stderr):
    if hasattr(stream, "reconfigure"):
        stream.reconfigure(encoding="utf-8", errors="replace")

root = Path(__file__).resolve().parents[1]
name = "NeoResourceUnpackTool"
executable = root / "dist" / (f"{name}.exe" if sys.platform == "win32" else f"{name}.app/Contents/MacOS/{name}")
report = root / "artifacts" / "bundled-smoke.json"
report.parent.mkdir(exist_ok=True)
report.unlink(missing_ok=True)
environment = os.environ.copy()
environment["QT_QPA_PLATFORM"] = "offscreen"
subprocess.run([str(executable), "--smoke-test", str(report)], env=environment, timeout=120, check=True)
assert json.loads(report.read_text("utf-8"))["ok"]
print("打包应用验证通过：Qt 界面、内置引擎和真实解压均正常。")
