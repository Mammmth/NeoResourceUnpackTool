"""A bundled-app smoke test: Qt + the bundled engine + a real unpack task."""
import json
import tempfile
import zipfile
from pathlib import Path


def main(report_path: str):
    from PySide6.QtWidgets import QApplication
    from PySide6.QtGui import QFontDatabase
    from . import config
    from .engine import find_engine
    from .gui import MainWindow, STYLE
    from .model import Settings
    from .workflow import UnpackTask

    report = Path(report_path)
    report.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="neo-smoke-") as directory:
        root = Path(directory)
        config.config_directory = lambda: root / "config"
        app = QApplication.instance() or QApplication([])
        app.setStyle("Fusion")
        app.setStyleSheet(STYLE)
        font_families = QFontDatabase.families()
        assert font_families, "The platform did not load any display fonts"
        window = MainWindow()
        window.show()
        app.processEvents()
        source = root / "smoke.jpg"
        with zipfile.ZipFile(source, "w") as archive:
            archive.writestr("包装/最终内容.txt", "Neo bundled app smoke test")
        result = UnpackTask([source], Settings()).run()
        assert (result.output / "最终内容.txt").read_text() == "Neo bundled app smoke test"
        import pyzipper
        encrypted = root / "utf8.zip"
        with pyzipper.AESZipFile(encrypted, "w", encryption=pyzipper.WZ_AES) as archive:
            archive.setpassword("中文密码".encode("utf-8"))
            archive.writestr("content.txt", "UTF-8 password verified")
        encrypted_result = UnpackTask([encrypted], Settings(passwords=["wrong", "中文密码"])).run()
        assert (encrypted_result.output / "content.txt").read_text() == "UTF-8 password verified"
        window.grab().save(str(report.with_suffix(".png")))
        report.write_text(json.dumps({"ok": True, "engine": str(find_engine()), "layers": result.layers,
                                      "utf8_zip_password": True, "font_families": len(font_families)}, ensure_ascii=False), "utf-8")
        window.close()
