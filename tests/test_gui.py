import os
import time

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtWidgets import QApplication, QDialog, QLineEdit, QMessageBox, QInputDialog, QFileDialog
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
    assert " has spaces " in dialog.list.item(1).text()
    assert dialog.input.echoMode() == QLineEdit.EchoMode.Normal
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


def test_delete_sources_option_persists_and_disables_repeat(app, make_zip, monkeypatch, tmp_path):
    monkeypatch.setattr(config, "config_directory", lambda: tmp_path / "config")
    window = MainWindow()
    assert not window.read_settings().delete_sources
    window.delete_sources.group.button(1).click()
    window.close()
    window = MainWindow()
    assert window.read_settings().delete_sources
    source = make_zip()
    window.select_files([str(source)])
    window.start_task()
    deadline = time.monotonic() + 15
    while window.busy and time.monotonic() < deadline:
        app.processEvents()
        time.sleep(0.01)
    assert not window.busy and not source.exists()
    assert window.output and window.open_button.isEnabled()
    assert not window.start.isEnabled() and not window.paths
    window.select_files([str(make_zip("next.zip"))])
    assert window.start.isEnabled()
    window.close()


def test_password_changes_save_without_closing(app, monkeypatch, tmp_path):
    monkeypatch.setattr(config, "config_directory", lambda: tmp_path / "config")
    window = MainWindow()
    dialog = PasswordDialog([], window, window.save_passwords)
    dialog.input.setText(" with spaces ")
    dialog.add_input()
    assert config.load_settings().passwords == [" with spaces "]
    monkeypatch.setattr(QInputDialog, "getText", lambda *args: ("edited", True))
    dialog.edit(dialog.list.item(0))
    assert config.load_settings().passwords == ["edited"]
    imported = tmp_path / "passwords.txt"
    imported.write_text("edited\n another \n\nthird", encoding="utf-8-sig")
    monkeypatch.setattr(QFileDialog, "getOpenFileName", lambda *args: (str(imported), ""))
    dialog.import_text()
    assert config.load_settings().passwords == ["edited", " another ", "third"]
    dialog.list.item(0).setSelected(True)
    dialog.remove()
    assert config.load_settings().passwords == [" another ", "third"]
    dialog.reject()  # Closing / Escape does not undo saved changes.
    assert config.load_settings().passwords == [" another ", "third"]
    window.close()


def test_password_save_failure_is_visible(app, monkeypatch, tmp_path):
    from neo_unpack import gui
    monkeypatch.setattr(config, "config_directory", lambda: tmp_path / "config")
    window = MainWindow()
    monkeypatch.setattr(gui, "save_settings", lambda _: (_ for _ in ()).throw(OSError("denied")))
    warnings = []
    monkeypatch.setattr(QMessageBox, "warning", lambda *args: warnings.append(args))
    dialog = PasswordDialog([], window, window.save_passwords)
    dialog.input.setText("kept in memory")
    dialog.add_input()
    assert warnings and "保存失败" in dialog.save_status.text()
    assert window.settings.passwords == ["kept in memory"]
    assert config.load_settings().passwords == []
    dialog.close()
    window.close()


def test_radio_settings_and_progress_states(app, monkeypatch, tmp_path):
    from neo_unpack.model import TaskProgress
    monkeypatch.setattr(config, "config_directory", lambda: tmp_path / "config")
    window = MainWindow()
    window.show()
    for index, (_, mode) in enumerate(window.mode.options):
        window.mode.group.button(index).click()
        app.processEvents()
        assert window.read_settings().output_mode == mode
        assert window.directory_widget.isVisible() == mode.startswith("fixed")
    window.depth.group.button(1).click()
    window.retention.group.button(1).click()
    assert window.read_settings().depth == "one"
    assert window.read_settings().keep_intermediates
    window.on_progress(TaskProgress("正在解压", "inner.7z", 2, 63))
    assert window.extraction_progress.value() == 63
    assert "第 2 层" in window.status.text() and "inner.7z" in window.status.text()
    for stage in ("检查压缩包", "检查密码", "兼容复试密码"):
        window.on_progress(TaskProgress("正在解压", "inner.7z", 2, 100))
        window.on_progress(TaskProgress(stage, "inner.7z", 2))
        assert window.extraction_progress.value() == 0
        assert window.extraction_status.text() == "解压进度：等待解压"
    window.on_progress(TaskProgress("正在解压", "inner.7z", 2, 37))
    window.on_failed("cancel", "已取消")
    assert window.extraction_progress.value() == 37
    assert "已停止" in window.extraction_status.text()
    window.close()


def test_password_counts_display_edit_delete_and_reload(app, monkeypatch, tmp_path):
    from neo_unpack.model import Settings
    monkeypatch.setattr(config, "config_directory", lambda: tmp_path / "config")
    config.save_settings(Settings(passwords=[" old ", "keep"], password_success_counts={" old ": 7, "keep": 4}))
    window = MainWindow()
    dialog = PasswordDialog(window.settings.passwords, window, window.save_passwords,
                            success_counts=window.settings.password_success_counts)
    assert "成功 7 次" in dialog.list.item(0).text() and dialog.passwords()[0] == " old "
    monkeypatch.setattr(QInputDialog, "getText", lambda *args: ("new", True))
    dialog.edit(dialog.list.item(0))
    assert "成功 0 次" in dialog.list.item(0).text()
    assert config.load_settings().password_success_counts == {"keep": 4}
    dialog.list.item(0).setSelected(True)
    dialog.remove()
    dialog.add_password(" old ")
    assert "成功 0 次" in dialog.list.item(1).text()
    dialog.close()
    window.close()
    reopened = MainWindow()
    assert reopened.read_settings().password_success_counts == {"keep": 4}
    reopened.close()


def test_worker_saves_counts_before_password_failure_and_resume(app, make_archive, monkeypatch, tmp_path):
    from neo_unpack.model import Settings
    monkeypatch.setattr(config, "config_directory", lambda: tmp_path / "config")
    inner = make_archive("7z", "inner", headers=True, name="inner")
    outer = make_archive("7z", "outer", headers=True, name="outer", files={"inner.7z": inner.read_bytes()})
    config.save_settings(Settings(passwords=["outer"]))
    window = MainWindow()
    window.select_files([str(outer)])
    window.start_task()
    deadline = time.monotonic() + 15
    while window.busy and time.monotonic() < deadline:
        app.processEvents()
        time.sleep(0.01)
    assert not window.busy and window.retry
    assert config.load_settings().password_success_counts == {"outer": 1}
    assert window.save_passwords(["outer", "inner"])
    window.start_task()
    deadline = time.monotonic() + 15
    while window.busy and time.monotonic() < deadline:
        app.processEvents()
        time.sleep(0.01)
    assert not window.busy and window.output
    assert config.load_settings().password_success_counts == {"outer": 1, "inner": 1}
    window.close()
