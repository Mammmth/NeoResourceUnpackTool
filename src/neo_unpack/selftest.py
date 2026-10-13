"""A bundled-app smoke test: Qt + the bundled engine + a real unpack task."""
import json
import tempfile
import time
import zipfile
from pathlib import Path


def main(report_path: str):
    from PySide6.QtWidgets import QApplication
    from PySide6.QtGui import QFontDatabase
    from . import config
    from .engine import find_engine
    from .gui import MainWindow, PasswordDialog, STYLE
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
        progress = []
        result = UnpackTask([source], Settings(), progress=progress.append).run()
        assert (result.output / "最终内容.txt").read_text() == "Neo bundled app smoke test"
        assert any(event.percent == 100 for event in progress)
        import pyzipper
        encrypted = root / "utf8.zip"
        with pyzipper.AESZipFile(encrypted, "w", encryption=pyzipper.WZ_AES) as archive:
            archive.setpassword("中文密码".encode("utf-8"))
            archive.writestr("content.txt", "UTF-8 password verified")
        encrypted_result = UnpackTask([encrypted], Settings(passwords=["wrong", "中文密码"])).run()
        assert (encrypted_result.output / "content.txt").read_text() == "UTF-8 password verified"
        class LegacyName(zipfile.ZipInfo):
            def _encodeFilenameFlags(self):
                return self.filename.encode("cp932"), self.flag_bits & ~0x800

        legacy = root / "legacy.zip"
        with zipfile.ZipFile(legacy, "w") as archive:
            archive.writestr(LegacyName("日本語のテスト.txt"), b"filename verified")
        for encoding in ("cp932", "auto"):
            name_result = UnpackTask([legacy], Settings(filename_encoding=encoding)).run()
            assert (name_result.output / "日本語のテスト.txt").read_bytes() == b"filename verified"
        # Exercise Worker -> UI signals and the actual password autosave path.
        passwords = PasswordDialog([], window, window.save_passwords)
        passwords.input.setText("中文密码")
        passwords.add_input()
        assert config.load_settings().passwords == ["中文密码"]
        passwords.close()
        window.delete_sources.group.button(1).click()
        window.select_files([str(encrypted)])
        window.start_task()
        deadline = time.monotonic() + 30
        while window.busy and time.monotonic() < deadline:
            app.processEvents()
            time.sleep(0.01)
        if window.busy:
            window.cancel_task()
            window.worker.wait(5000)
            raise AssertionError("GUI extraction timed out")
        assert window.output and (window.output / "content.txt").is_file()
        assert window.extraction_progress.value() == 100
        assert "密码（中文密码）验证成功" in window.log.toPlainText()
        assert not encrypted.exists() and not window.start.isEnabled()
        assert config.load_settings().delete_sources
        window.grab().save(str(report.with_suffix(".png")))
        window.filename_encoding.showPopup()
        app.processEvents()
        window.filename_encoding.view().window().grab().save(str(report.with_name(report.stem + "-encoding.png")))
        window.filename_encoding.hidePopup()
        passwords = PasswordDialog(window.settings.passwords, window, window.save_passwords)
        passwords.show()
        app.processEvents()
        passwords.grab().save(str(report.with_name(report.stem + "-passwords.png")))
        passwords.close()
        report.write_text(json.dumps({"ok": True, "engine": str(find_engine()), "layers": result.layers,
                                      "utf8_zip_password": True, "filename_encoding": True,
                                      "gui_progress": True, "password_autosave": True,
                                      "delete_sources": True,
                                      "font_families": len(font_families)}, ensure_ascii=False), "utf-8")
        window.close()
