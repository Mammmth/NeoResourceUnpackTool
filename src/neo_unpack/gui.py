from __future__ import annotations

import sys
from pathlib import Path
from threading import Event

from PySide6.QtCore import Qt, QThread, Signal, QUrl
from PySide6.QtGui import QDesktopServices, QFont, QCloseEvent
from PySide6.QtWidgets import (
    QApplication, QCheckBox, QComboBox, QDialog, QDialogButtonBox, QFileDialog,
    QFrame, QHBoxLayout, QInputDialog, QLabel, QLineEdit, QListWidget,
    QListWidgetItem, QMainWindow, QMessageBox, QPlainTextEdit, QProgressBar,
    QPushButton, QSizePolicy, QVBoxLayout, QWidget,
)

from .config import load_settings, save_settings
from .model import Cancelled, FILENAME_ENCODINGS, PasswordRequired, Settings, UnpackError
from .volumes import make_group
from .workflow import UnpackTask


STYLE = """
QMainWindow, QDialog { background: #F5F5F0; color: #20312D; }
QWidget { font-size: 13px; color: #20312D; }
QFrame#card { background: #FFFFFF; border: 1px solid #DDE4DD; border-radius: 14px; }
QFrame#drop { background: #EEF3ED; border: 2px dashed #A6BBA9; border-radius: 14px; }
QLabel#eyebrow { color: #527661; font-size: 11px; font-weight: 700; letter-spacing: 2px; }
QLabel#title { font-size: 28px; font-weight: 700; }
QLabel#section { font-size: 16px; font-weight: 600; }
QLabel#muted { color: #6A7770; }
QLabel#status { font-size: 16px; font-weight: 600; }
QPushButton { background: #FFFFFF; border: 1px solid #C9D5CA; border-radius: 8px; padding: 9px 16px; }
QPushButton:hover { background: #EAF0E8; border-color: #8EAA94; }
QPushButton:disabled { color: #9CA59F; background: #F0F2ED; border-color: #E0E5DE; }
QPushButton#primary { background: #255D45; color: white; border: none; font-weight: 600; padding: 12px 24px; }
QPushButton#primary:hover { background: #1D4C38; }
QPushButton#primary:disabled { background: #A3B9AA; }
QComboBox, QLineEdit, QPlainTextEdit, QListWidget { background: #FAFBF8; border: 1px solid #D6DFD4; border-radius: 7px; padding: 7px; selection-background-color: #D3E5D4; }
QComboBox { min-height: 22px; }
QComboBox::drop-down { border: none; width: 25px; }
QProgressBar { border: none; background: #E5EBE1; border-radius: 3px; min-height: 6px; max-height: 6px; }
QProgressBar::chunk { background: #38744F; border-radius: 3px; }
QCheckBox { spacing: 8px; }
QToolTip { background: #FFFFFF; color: #20312D; border: 1px solid #D6DFD4; }
"""


def label(text: str, role="", wrap=False) -> QLabel:
    widget = QLabel(text)
    widget.setTextFormat(Qt.TextFormat.PlainText)
    widget.setObjectName(role)
    widget.setWordWrap(wrap)
    return widget


class DropArea(QFrame):
    selected = Signal(list)

    def __init__(self):
        super().__init__()
        self.setObjectName("drop")
        self.setAcceptDrops(True)
        self.setMinimumHeight(180)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 22, 24, 22)
        self.heading = label("把压缩包拖到这里", "section")
        self.heading.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        self.heading.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.detail = label("支持 ZIP / RAR / 7z，包括改后缀与前置载体\n分卷请一次拖入同一组的全部文件", "muted", True)
        self.detail.setAlignment(Qt.AlignmentFlag.AlignCenter)
        button = QPushButton("选择文件或全部分卷")
        button.clicked.connect(self.browse)
        layout.addWidget(self.heading)
        layout.addWidget(self.detail)
        layout.addWidget(button, alignment=Qt.AlignmentFlag.AlignCenter)

    def browse(self):
        paths, _ = QFileDialog.getOpenFileNames(self, "选择一个压缩包或同一组全部分卷", "", "所有文件 (*)")
        if paths:
            self.selected.emit(paths)

    def dragEnterEvent(self, event):
        if self.isEnabled() and event.mimeData().hasUrls() and all(u.isLocalFile() for u in event.mimeData().urls()):
            event.acceptProposedAction()

    def dropEvent(self, event):
        if self.isEnabled():
            self.selected.emit([u.toLocalFile() for u in event.mimeData().urls()])
            event.acceptProposedAction()


class PasswordDialog(QDialog):
    def __init__(self, passwords: list[str], parent=None):
        super().__init__(parent)
        self.setWindowTitle("管理常用密码")
        self.resize(530, 470)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 24, 24, 24)
        layout.setSpacing(14)
        layout.addWidget(label("常用密码", "section"))
        layout.addWidget(label("按顺序尝试；成功的密码在本次任务后续层优先使用。", "muted", True))
        self.list = QListWidget()
        self.list.setSelectionMode(QListWidget.SelectionMode.ExtendedSelection)
        self.list.itemDoubleClicked.connect(self.edit)
        layout.addWidget(self.list)
        row = QHBoxLayout()
        self.input = QLineEdit()
        self.input.setPlaceholderText("输入一个密码，按回车添加")
        self.input.setEchoMode(QLineEdit.EchoMode.Password)
        self.input.returnPressed.connect(self.add_input)
        add = QPushButton("添加")
        add.clicked.connect(self.add_input)
        row.addWidget(self.input)
        row.addWidget(add)
        layout.addLayout(row)
        row = QHBoxLayout()
        import_button = QPushButton("从 TXT 导入")
        import_button.clicked.connect(self.import_text)
        delete = QPushButton("删除所选")
        delete.clicked.connect(self.remove)
        self.show_passwords = QCheckBox("显示密码")
        self.show_passwords.toggled.connect(self.refresh)
        row.addWidget(import_button)
        row.addWidget(delete)
        row.addStretch()
        row.addWidget(self.show_passwords)
        layout.addLayout(row)
        layout.addWidget(label("双击可编辑。TXT 每行一个密码，保留密码中的空格。\n密码仅保存在本机配置中，可随时删除。", "muted", True))
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
        buttons.button(QDialogButtonBox.StandardButton.Save).setText("保存密码库")
        buttons.button(QDialogButtonBox.StandardButton.Cancel).setText("取消")
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        for password in passwords:
            self.add_password(password)

    def passwords(self) -> list[str]:
        return [self.list.item(i).data(Qt.ItemDataRole.UserRole) for i in range(self.list.count())]

    def accept(self):
        self.add_input()
        super().accept()

    def add_password(self, password):
        if password and password not in self.passwords():
            item = QListWidgetItem()
            item.setData(Qt.ItemDataRole.UserRole, password)
            self.list.addItem(item)
            self.refresh()

    def add_input(self):
        self.add_password(self.input.text())
        self.input.clear()

    def refresh(self):
        show = self.show_passwords.isChecked()
        self.input.setEchoMode(QLineEdit.EchoMode.Normal if show else QLineEdit.EchoMode.Password)
        for index in range(self.list.count()):
            item = self.list.item(index)
            item.setText(f"{index + 1:02d}   " + (item.data(Qt.ItemDataRole.UserRole) if show else "••••••••"))

    def edit(self, item):
        value, okay = QInputDialog.getText(self, "编辑密码", "密码", QLineEdit.EchoMode.Password, item.data(Qt.ItemDataRole.UserRole))
        if okay and value:
            if value != item.data(Qt.ItemDataRole.UserRole) and value in self.passwords():
                return
            item.setData(Qt.ItemDataRole.UserRole, value)
            self.refresh()

    def remove(self):
        for item in self.list.selectedItems():
            self.list.takeItem(self.list.row(item))
        self.refresh()

    def import_text(self):
        path, _ = QFileDialog.getOpenFileName(self, "导入密码库", "", "文本文件 (*.txt);;所有文件 (*)")
        if path:
            try:
                text = Path(path).read_text("utf-8-sig")
                for password in text.splitlines():
                    self.add_password(password)
            except (OSError, UnicodeError):
                QMessageBox.warning(self, "导入失败", "请使用 UTF-8 编码的 TXT 文件，每行一个密码。")


class Worker(QThread):
    message = Signal(str)
    completed = Signal(object)
    failed = Signal(str, str)
    ready = Signal(object)

    def __init__(self, paths, settings, task=None):
        super().__init__()
        self.paths, self.settings, self.task = paths, settings, task
        self.cancel = task.cancel if task else Event()

    def run(self):
        try:
            if self.task is None:
                self.task = UnpackTask([Path(p) for p in self.paths], self.settings, cancel=self.cancel, log=self.message.emit)
            else:
                self.task.settings = self.settings
                self.task.log = self.message.emit
            self.ready.emit(self.task)
            self.completed.emit(self.task.run())
        except PasswordRequired as error:
            self.failed.emit("password", str(error))
        except Cancelled as error:
            self.failed.emit("cancel", str(error))
        except (UnpackError, OSError, ValueError) as error:
            self.failed.emit("error", str(error))
        except Exception:
            # Never expose a traceback containing subprocess arguments/passwords.
            self.failed.emit("error", "处理遇到未预期错误。任务缓存已保留，请保留样本用于排查。")


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Neo · 自动解压")
        help_menu = self.menuBar().addMenu("帮助")
        help_menu.addAction("第三方组件与许可证", self.open_licenses)
        self.resize(950, 900)
        self.setMinimumSize(850, 860)
        self.paths: list[str] = []
        self.task: UnpackTask | None = None
        self.worker: Worker | None = None
        self.output: Path | None = None
        self.busy = False
        self.retry = False
        config_error = False
        try:
            self.settings = load_settings()
        except (OSError, ValueError, TypeError, UnpackError):
            self.settings = Settings()
            config_error = True
        central = QWidget()
        self.setCentralWidget(central)
        layout = QVBoxLayout(central)
        layout.setContentsMargins(32, 26, 32, 24)
        layout.setSpacing(18)
        layout.addWidget(label("NEO RESOURCE UNPACK TOOL", "eyebrow"))
        layout.addWidget(label("层层解开，只留下你要的内容。", "title"))
        layout.addWidget(label("识别真实格式 · 还原隐写压缩包 · 自动尝试密码 · 按策略递归", "muted"))
        self.drop = DropArea()
        self.drop.selected.connect(self.select_files)
        layout.addWidget(self.drop)
        row = QHBoxLayout()
        row.setSpacing(18)
        card = QFrame()
        card.setObjectName("card")
        card.setMinimumHeight(215)
        controls = QVBoxLayout(card)
        controls.setContentsMargins(20, 18, 20, 18)
        controls.setSpacing(10)
        controls.addWidget(label("解压设置", "section"))
        self.mode = QComboBox()
        for text, value in [("解压到源目录下的新文件夹", "folder"), ("直接解压到源目录", "direct"), ("解压到固定目录", "fixed"), ("固定目录下新建文件夹", "fixed_folder")]:
            self.mode.addItem(text, value)
        self.mode.setCurrentIndex(self.mode.findData(self.settings.output_mode))
        self.mode.currentIndexChanged.connect(self.update_fixed)
        controls.addWidget(self.mode)
        self.directory_widget = QWidget()
        directory_row = QHBoxLayout(self.directory_widget)
        directory_row.setContentsMargins(0, 0, 0, 0)
        self.directory = QLineEdit(self.settings.fixed_directory)
        self.directory.setPlaceholderText("选择固定输出目录")
        self.browse_directory = QPushButton("浏览")
        self.browse_directory.clicked.connect(self.choose_directory)
        directory_row.addWidget(self.directory)
        directory_row.addWidget(self.browse_directory)
        controls.addWidget(self.directory_widget)
        options = QHBoxLayout()
        self.depth = QComboBox()
        self.depth.addItem("默认策略 · 自动递归", "auto")
        self.depth.addItem("仅解压 1 层", "one")
        self.depth.setCurrentIndex(self.depth.findData(self.settings.depth))
        self.retention = QComboBox()
        self.retention.addItem("仅保留最终结果", False)
        self.retention.addItem("保留中间产物", True)
        self.retention.setCurrentIndex(int(self.settings.keep_intermediates))
        options.addWidget(self.depth)
        options.addWidget(self.retention)
        controls.addLayout(options)
        encoding_row = QHBoxLayout()
        encoding_label = label("文件名编码")
        self.filename_encoding = QComboBox()
        for value, text in FILENAME_ENCODINGS.items():
            self.filename_encoding.addItem(text, value)
        self.filename_encoding.setCurrentIndex(self.filename_encoding.findData(self.settings.filename_encoding))
        self.filename_encoding.setToolTip("用于旧 ZIP 的文件名乱码。优先保留压缩包自带的 Unicode 名称；不改变文件内容或密码。自动检测可能不准确。")
        encoding_label.setBuddy(self.filename_encoding)
        encoding_row.addWidget(encoding_label)
        encoding_row.addWidget(self.filename_encoding, 1)
        controls.addLayout(encoding_row)
        controls.addWidget(label("自动去掉包装目录，保留内容自身的目录结构。", "muted", True))
        row.addWidget(card, 3)
        password_card = QFrame()
        password_card.setObjectName("card")
        passwords = QVBoxLayout(password_card)
        passwords.setContentsMargins(20, 18, 20, 18)
        passwords.addWidget(label("常用密码库", "section"))
        self.password_count = label("", "muted")
        passwords.addWidget(self.password_count)
        passwords.addWidget(label("自动依次尝试。未匹配时可补充密码，继续当前任务。", "muted", True))
        passwords.addStretch()
        self.password_button = QPushButton("管理密码")
        self.password_button.clicked.connect(self.manage_passwords)
        passwords.addWidget(self.password_button)
        row.addWidget(password_card, 2)
        layout.addLayout(row)
        self.settings_widgets = [card, self.password_button]
        self.status = label("等待选择文件", "status", True)
        layout.addWidget(self.status)
        self.progress = QProgressBar()
        self.progress.setTextVisible(False)
        self.progress.setRange(0, 1)
        self.progress.setValue(0)
        layout.addWidget(self.progress)
        self.log = QPlainTextEdit()
        self.log.setReadOnly(True)
        self.log.setPlaceholderText("运行过程会显示在这里。原始文件始终保留。")
        self.log.setMinimumHeight(100)
        self.log.setMaximumBlockCount(2000)
        layout.addWidget(self.log, 1)
        footer = QHBoxLayout()
        self.open_button = QPushButton("打开结果目录")
        self.open_button.clicked.connect(self.open_output)
        self.open_button.setEnabled(False)
        self.cache_button = QPushButton("打开任务缓存")
        self.cache_button.clicked.connect(self.open_cache)
        self.cache_button.setVisible(False)
        self.stop = QPushButton("取消")
        self.stop.clicked.connect(self.cancel_task)
        self.stop.setEnabled(False)
        self.start = QPushButton("开始解压")
        self.start.setObjectName("primary")
        self.start.clicked.connect(self.start_task)
        self.start.setEnabled(False)
        footer.addWidget(self.open_button)
        footer.addWidget(self.cache_button)
        footer.addStretch()
        footer.addWidget(self.stop)
        footer.addWidget(self.start)
        layout.addLayout(footer)
        self.update_fixed()
        self.update_password_count()
        if config_error:
            self.log.appendPlainText("已有配置无法读取，当前使用默认设置；原配置文件尚未修改。")

    def update_fixed(self):
        enabled = self.mode.currentData().startswith("fixed")
        self.directory.setEnabled(enabled)
        self.browse_directory.setEnabled(enabled)
        self.directory_widget.setVisible(enabled)

    def update_password_count(self):
        self.password_count.setText(f"已保存 {len(self.settings.passwords)} 个密码")

    def choose_directory(self):
        directory = QFileDialog.getExistingDirectory(self, "选择输出目录", self.directory.text())
        if directory:
            self.directory.setText(directory)

    def manage_passwords(self):
        dialog = PasswordDialog(self.settings.passwords, self)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            self.settings.passwords = dialog.passwords()
            try:
                save_settings(self.settings)
            except (OSError, UnpackError):
                QMessageBox.warning(self, "保存失败", "密码库暂存在本窗口，无法写入本机配置目录。")
            self.update_password_count()

    def read_settings(self):
        return Settings(
            output_mode=self.mode.currentData(), fixed_directory=self.directory.text(),
            depth=self.depth.currentData(), keep_intermediates=self.retention.currentData(),
            passwords=list(self.settings.passwords),
            filename_encoding=self.filename_encoding.currentData(),
        )

    def select_files(self, paths):
        if self.busy:
            return
        try:
            group = make_group([Path(p) for p in paths])
        except (UnpackError, OSError) as error:
            QMessageBox.warning(self, "无法选择这些文件", str(error))
            return
        self.paths = [str(p) for p in group.paths]
        self.task, self.output, self.retry = None, None, False
        self.drop.heading.setText(group.entry.name)
        self.drop.heading.setToolTip(group.entry.name)
        self.drop.detail.setText(f"已选择 {len(group.paths)} 个文件 · " + ("同一组分卷" if group.style != "single" else "单个文件"))
        self.log.clear()
        self.status.setText("已就绪")
        self.progress.setRange(0, 1)
        self.progress.setValue(0)
        self.start.setText("开始解压")
        self.start.setEnabled(True)
        self.open_button.setEnabled(False)
        self.cache_button.setVisible(False)

    def set_busy(self, busy):
        self.busy = busy
        self.drop.setEnabled(not busy)
        for widget in self.settings_widgets:
            widget.setEnabled(not busy)
        self.start.setEnabled(not busy and bool(self.paths))
        self.stop.setEnabled(busy)

    def start_task(self):
        if self.busy or (self.worker and self.worker.isRunning()):
            return
        try:
            self.settings = self.read_settings()
            self.settings.validate()
            save_settings(self.settings)
        except (UnpackError, OSError) as error:
            QMessageBox.warning(self, "请检查配置", str(error))
            return
        task = self.task if self.retry else None
        if task is None:
            self.task = None
        if task:
            task.cancel.clear()
        self.worker = Worker(self.paths, self.settings, task)
        self.worker.message.connect(self.on_message)
        self.worker.ready.connect(self.on_ready)
        self.worker.completed.connect(self.on_completed)
        self.worker.failed.connect(self.on_failed)
        self.worker.finished.connect(self.on_worker_finished)
        self.set_busy(True)
        self.open_button.setEnabled(False)
        self.cache_button.setVisible(False)
        self.progress.setRange(0, 0)
        self.status.setText("正在识别与处理…")
        self.worker.start()

    def on_ready(self, task):
        self.task = task

    def on_message(self, message):
        self.log.appendPlainText(message)
        self.status.setText(message)

    def on_completed(self, result):
        self.output, self.retry = result.output, False
        self.progress.setRange(0, 1)
        self.progress.setValue(1)
        self.status.setText(f"已完成 · {result.archives} 个压缩包 / {result.layers} 层")
        self.log.appendPlainText(f"最终内容：{result.output}")
        self.open_button.setEnabled(True)
        self.start.setText("再次解压")

    def on_failed(self, kind, message):
        self.retry = self.task is not None
        self.progress.setRange(0, 1)
        self.progress.setValue(0)
        self.status.setText({"password": "等待补充密码", "cancel": "已取消", "error": "处理未完成"}[kind])
        self.log.appendPlainText(message)
        if self.task:
            self.log.appendPlainText(f"任务缓存：{self.task.session}")
            self.cache_button.setVisible(True)
        self.start.setText("继续当前任务" if self.retry else "重试")
        if kind == "password":
            self.log.appendPlainText("点击“管理密码”添加密码，再点击“继续当前任务”。")

    def on_worker_finished(self):
        self.set_busy(False)

    def cancel_task(self):
        if self.worker:
            self.worker.cancel.set()
            self.stop.setEnabled(False)
            self.status.setText("正在取消并保留缓存…")

    def open_output(self):
        if self.output:
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(self.output)))

    def open_cache(self):
        if self.task:
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(self.task.session)))

    def open_licenses(self):
        root = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parents[2]))
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(root / "third_party")))

    def closeEvent(self, event: QCloseEvent):
        if self.busy:
            self.cancel_task()
            self.status.setText("正在停止任务，请稍后再关闭窗口。")
            event.ignore()
            return
        try:
            settings = self.read_settings()
            settings.validate()
            save_settings(settings)
        except (OSError, UnpackError):
            pass
        event.accept()


def main():
    application = QApplication.instance() or QApplication(sys.argv)
    application.setApplicationName("NeoResourceUnpackTool")
    application.setOrganizationName("NeoResourceUnpackTool")
    application.setStyle("Fusion")
    application.setStyleSheet(STYLE)
    window = MainWindow()
    window.show()
    raise SystemExit(application.exec())


if __name__ == "__main__":
    main()
