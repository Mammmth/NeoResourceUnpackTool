import os
import time

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtWidgets import QApplication, QDialog
from neo_unpack import config
from neo_unpack.gui import MainWindow, PasswordDialog, STYLE


@pytest.fixture
def app():
    application = QApplication.instance() or QApplication([])
    application.setStyle("Fusion")
    application.setStyleSheet(STYLE)
    return application


def test_window_runs_real_task(app, make_zip, monkeypatch, tmp_path):
    monkeypatch.setattr(config, "config_directory", lambda: tmp_path / "config")
    window = MainWindow()
    window.show()
    app.processEvents()
    window.select_files([str(make_zip())])
    window.start_task()
    deadline = time.monotonic() + 15
    while window.busy and time.monotonic() < deadline:
        app.processEvents()
        time.sleep(0.01)
    assert not window.busy
    assert window.output and (window.output / "hello.txt").is_file()
    assert window.open_button.isEnabled()
    # A new run failing before task creation must not reuse the completed task.
    from pathlib import Path
    Path(window.paths[0]).unlink()
    window.start_task()
    deadline = time.monotonic() + 5
    while window.busy and time.monotonic() < deadline:
        app.processEvents()
        time.sleep(0.01)
    assert window.task is None and not window.retry
    window.close()


def test_password_editor_preserves_spaces(app):
    dialog = PasswordDialog(["original"])
    dialog.input.setText(" has spaces ")
    dialog.add_input()
    dialog.add_password("original")
    assert dialog.passwords() == ["original", " has spaces "]
    assert " has spaces " not in dialog.list.item(1).text()
    dialog.show_passwords.setChecked(True)
    assert " has spaces " in dialog.list.item(1).text()
    dialog.close()


def test_filename_encoding_control_persists(app, monkeypatch, tmp_path):
    monkeypatch.setattr(config, "config_directory", lambda: tmp_path / "config")
    window = MainWindow()
    assert window.filename_encoding.currentData() == "default"
    window.filename_encoding.setCurrentIndex(window.filename_encoding.findData("cp932"))
    config.save_settings(window.read_settings())
    window.close()
    reopened = MainWindow()
    assert reopened.filename_encoding.currentData() == "cp932"
    reopened.close()
