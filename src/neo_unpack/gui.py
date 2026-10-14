from __future__ import annotations

import sys
from pathlib import Path
from threading import Event

from PySide6.QtCore import QPointF, Qt, QThread, Signal, QUrl
from PySide6.QtGui import QColor, QDesktopServices, QCloseEvent, QPainter, QPen, QPolygonF
from PySide6.QtWidgets import (
    QApplication, QButtonGroup, QComboBox, QDialog, QDialogButtonBox, QFileDialog,
    QFrame, QGridLayout, QHBoxLayout, QInputDialog, QLabel, QLineEdit, QListWidget,
    QListView, QListWidgetItem, QMainWindow, QMessageBox, QPlainTextEdit, QProgressBar,
    QPushButton, QRadioButton, QScrollArea, QSizePolicy, QSplitter, QVBoxLayout, QWidget,
)

from .config import load_settings, save_settings
from .model import Cancelled, FILENAME_ENCODINGS, PasswordRequired, Settings, TaskProgress, UnpackError
from .volumes import make_group
from .workflow import UnpackTask


STYLE = """
QMainWindow, QDialog, QScrollArea { background: #F5F5F0; color: #20312D; }
QWidget { background: #F5F5F0; font-size: 13px; font-weight: 400; color: #20312D; }
QLabel, QRadioButton { background: transparent; }
QFrame#drop { background: #EEF3ED; border: 2px dashed #A6BBA9; border-radius: 10px; }
QLabel#title { font-size: 22px; font-weight: 700; }
QLabel#section { font-size: 16px; font-weight: 600; }
QLabel#muted { color: #59675F; }
QLabel#status { font-size: 15px; font-weight: 600; }
QPushButton { background: #FAFBF8; border: 1px solid #B8C8BC; border-radius: 6px; padding: 8px 12px; }
QPushButton:hover { background: #EAF0E8; border-color: #6E9177; }
QPushButton:focus, QComboBox:focus, QLineEdit:focus { border: 1px solid #255D45; }
QPushButton:pressed { background: #D3E5D4; }
QPushButton:disabled { color: #88958C; background: #F0F2ED; border-color: #DDE4DB; }
QPushButton#primary { background: #255D45; color: #FAFBF8; border: 1px solid #255D45; font-weight: 600; }
QPushButton#primary:hover { background: #1D4C38; }
QPushButton#primary:disabled { background: #A3B9AA; border-color: #A3B9AA; }
QComboBox, QLineEdit, QPlainTextEdit, QListWidget { color: #20312D; background: #FAFBF8; border: 1px solid #C7D3C8; border-radius: 6px; padding: 7px; selection-background-color: #D3E5D4; selection-color: #20312D; }
QComboBox { min-height: 22px; padding-right: 24px; }
QComboBox::drop-down { border: none; width: 24px; background: transparent; }
QComboBox::down-arrow { image: none; }
QComboBox QAbstractItemView { background: #FAFBF8; color: #20312D; border: 1px solid #A6BBA9; outline: 0; selection-background-color: #D3E5D4; selection-color: #20312D; }
QComboBox QAbstractItemView::item { min-height: 28px; padding: 3px 8px; }
QComboBox QAbstractItemView::item:selected, QListWidget::item:selected { background: #D3E5D4; color: #20312D; }
QProgressBar { border: 1px solid #CDD9CE; background: #E5EBE1; border-radius: 4px; min-height: 18px; max-height: 18px; text-align: center; color: #20312D; }
QProgressBar::chunk { background: #A6C9AF; border-radius: 3px; }
QProgressBar#overall { min-height: 6px; max-height: 6px; border: none; }
QProgressBar#overall::chunk { background: #38744F; }
QRadioButton { spacing: 7px; padding: 4px 0; }
QRadioButton::indicator { width: 16px; height: 16px; border-radius: 9px; border: 1px solid #7A9181; background: #FAFBF8; }
QRadioButton::indicator:checked { background: qradialgradient(cx:0.5, cy:0.5, radius:0.5, fx:0.5, fy:0.5, stop:0 #255D45, stop:0.48 #255D45, stop:0.5 #FAFBF8, stop:1 #FAFBF8); border-color: #255D45; }
QRadioButton::indicator:hover { border-color: #255D45; }
QRadioButton:focus { color: #255D45; text-decoration: underline; }
QRadioButton:disabled { color: #88958C; }
QRadioButton::indicator:disabled { border-color: #B8C8BC; }
QSplitter::handle { background: #DDE4DB; }
QToolTip { background: #FAFBF8; color: #20312D; border: 1px solid #C7D3C8; }
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


class EncodingComboBox(QComboBox):
    def paintEvent(self, event):
        super().paintEvent(event)
        # Native arrows can disappear when a system dark palette meets light QSS.
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(QPen(QColor("#527661" if self.isEnabled() else "#88958C"), 1.5))
        x, y = self.width() - 13, self.height() / 2
        painter.drawPolyline(QPolygonF([QPointF(x - 4, y - 2), QPointF(x, y + 2), QPointF(x + 4, y - 2)]))


class ChoiceGroup(QWidget):
    changed = Signal()

    def __init__(self, options, value):
        super().__init__()
        self.group = QButtonGroup(self)
        self.options = options
        layout = QGridLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setHorizontalSpacing(18)
        layout.setVerticalSpacing(2)
        for index, (text, data) in enumerate(options):
            button = QRadioButton(text)
            self.group.addButton(button, index)
            layout.addWidget(button, index // 2, index % 2)
            button.setChecked(data == value)
        self.group.buttonToggled.connect(lambda _, checked: self.changed.emit() if checked else None)

    def value(self):
        return self.options[self.group.checkedId()][1]


class PasswordDialog(QDialog):
    def __init__(self, passwords: list[str], parent=None, on_change=None):
        super().__init__(parent)
        self.on_change = on_change
        self.setWindowTitle("管理密码")
        self.resize(560, 480)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 24, 24, 24)
        layout.setSpacing(14)
        layout.addWidget(label("常用密码", "section"))
        layout.addWidget(label("按顺序尝试，成功后在当前任务中优先使用。", "muted", True))
        self.list = QListWidget()
        self.list.setSelectionMode(QListWidget.SelectionMode.ExtendedSelection)
        self.list.itemDoubleClicked.connect(self.edit)
        layout.addWidget(self.list)
        row = QHBoxLayout()
        self.input = QLineEdit()
        self.input.setPlaceholderText("输入密码，按回车添加")
        self.input.returnPressed.connect(self.add_input)
        add = QPushButton("添加")
        add.setAutoDefault(False)
        add.clicked.connect(self.add_input)
        row.addWidget(self.input)
        row.addWidget(add)
        layout.addLayout(row)
        row = QHBoxLayout()
        import_button = QPushButton("从 TXT 导入")
        import_button.setAutoDefault(False)
        import_button.clicked.connect(self.import_text)
        delete = QPushButton("删除所选")
        delete.setAutoDefault(False)
        delete.clicked.connect(self.remove)
        row.addWidget(import_button)
        row.addWidget(delete)
        row.addStretch()
        layout.addLayout(row)
        layout.addWidget(label("双击编辑。TXT 每行一个密码，保留首尾空格。修改后自动保存。", "muted", True))
        self.save_status = label("", "muted", True)
        layout.addWidget(self.save_status)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.button(QDialogButtonBox.StandardButton.Close).setText("关闭")
        buttons.button(QDialogButtonBox.StandardButton.Close).setAutoDefault(False)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        for password in passwords:
            self.add_password(password, notify=False)

    def passwords(self) -> list[str]:
        return [self.list.item(i).data(Qt.ItemDataRole.UserRole) for i in range(self.list.count())]

    def commit(self):
        self.refresh()
        if self.on_change:
            saved = self.on_change(self.passwords())
            self.save_status.setText("已自动保存" if saved else "保存失败，修改暂存在本窗口。请检查配置目录权限。")

    def add_password(self, password, *, notify=True):
        if password and password not in self.passwords():
            item = QListWidgetItem()
            item.setData(Qt.ItemDataRole.UserRole, password)
            self.list.addItem(item)
            self.refresh()
            if notify:
                self.commit()

    def add_input(self):
        self.add_password(self.input.text())
        self.input.clear()

    def refresh(self):
        for index in range(self.list.count()):
            item = self.list.item(index)
            item.setText(f"{index + 1:02d}   " + item.data(Qt.ItemDataRole.UserRole))

    def edit(self, item):
        value, okay = QInputDialog.getText(self, "编辑密码", "密码", QLineEdit.EchoMode.Normal, item.data(Qt.ItemDataRole.UserRole))
        if okay and value and value != item.data(Qt.ItemDataRole.UserRole):
            if value in self.passwords():
                return
            item.setData(Qt.ItemDataRole.UserRole, value)
            self.commit()

    def remove(self):
        selected = self.list.selectedItems()
        for item in selected:
            self.list.takeItem(self.list.row(item))
        if selected:
            self.commit()

    def import_text(self):
        path, _ = QFileDialog.getOpenFileName(self, "导入密码库", "", "文本文件 (*.txt);;所有文件 (*)")
        if path:
            try:
                text = Path(path).read_text("utf-8-sig")
                before = self.passwords()
                for password in text.splitlines():
                    self.add_password(password, notify=False)
                if self.passwords() != before:
                    self.commit()
            except (OSError, UnicodeError):
                QMessageBox.warning(self, "导入失败", "请使用 UTF-8 编码的 TXT 文件，每行一个密码。")


class Worker(QThread):
    message = Signal(str)
    progress = Signal(object)
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
                self.task = UnpackTask([Path(p) for p in self.paths], self.settings, cancel=self.cancel, log=self.message.emit, progress=self.progress.emit)
            else:
                self.task.settings = self.settings
                self.task.log = self.message.emit
                self.task.progress = self.progress.emit
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
        self.resize(1180, 800)
        self.setMinimumSize(1000, 700)
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
        layout.setContentsMargins(24, 20, 24, 20)
        layout.setSpacing(16)
        layout.addWidget(label("Neo 自动解压", "title"))
        self.splitter = QSplitter(Qt.Orientation.Horizontal)
        layout.addWidget(self.splitter, 1)
        left_scroll = QScrollArea()
        left_scroll.setWidgetResizable(True)
        left_scroll.setFrameShape(QFrame.Shape.NoFrame)
        left_scroll.setMinimumWidth(450)
        left = QWidget()
        left_scroll.setWidget(left)
        actions = QVBoxLayout(left)
        actions.setContentsMargins(0, 0, 18, 0)
        actions.setSpacing(14)
        self.drop = DropArea()
        self.drop.selected.connect(self.select_files)
        actions.addWidget(self.drop)
        card = QWidget()
        controls = QVBoxLayout(card)
        controls.setContentsMargins(0, 0, 0, 0)
        controls.setSpacing(8)
        controls.addWidget(label("输出位置", "section"))
        self.mode = ChoiceGroup([
            ("源目录新建文件夹", "folder"), ("直接解压到源目录", "direct"),
            ("直接解压到固定目录", "fixed"), ("固定目录新建文件夹", "fixed_folder"),
        ], self.settings.output_mode)
        self.mode.changed.connect(self.update_fixed)
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
        controls.addWidget(label("解压层数", "section"))
        self.depth = ChoiceGroup([("自动递归", "auto"), ("仅解压一层", "one")], self.settings.depth)
        controls.addWidget(self.depth)
        controls.addWidget(label("保留内容", "section"))
        self.retention = ChoiceGroup([("仅保留最终结果", False), ("保留中间产物", True)], self.settings.keep_intermediates)
        controls.addWidget(self.retention)
        controls.addWidget(label("原文件", "section"))
        self.delete_sources = ChoiceGroup([
            ("保留原文件", False), ("解压完成后删除", True),
        ], self.settings.delete_sources)
        self.delete_sources.setToolTip("结果成功写入后，直接删除本次选中的原文件或全部分卷；失败、取消时保留。")
        controls.addWidget(self.delete_sources)
        encoding_row = QHBoxLayout()
        encoding_label = label("文件名编码")
        self.filename_encoding = EncodingComboBox()
        self.filename_encoding.setView(QListView())
        for value, text in FILENAME_ENCODINGS.items():
            self.filename_encoding.addItem(text, value)
        self.filename_encoding.setCurrentIndex(self.filename_encoding.findData(self.settings.filename_encoding))
        self.filename_encoding.setToolTip("用于旧 ZIP 文件名乱码。优先保留已有 Unicode 名称；自动检测可能不准确。")
        encoding_label.setBuddy(self.filename_encoding)
        encoding_row.addWidget(encoding_label)
        encoding_row.addWidget(self.filename_encoding, 1)
        controls.addLayout(encoding_row)
        actions.addWidget(card)
        passwords = QHBoxLayout()
        self.password_count = label("", "muted")
        passwords.addWidget(self.password_count, 1)
        self.password_button = QPushButton("管理密码")
        self.password_button.clicked.connect(self.manage_passwords)
        passwords.addWidget(self.password_button)
        actions.addLayout(passwords)
        self.settings_widgets = [card, self.password_button]
        actions.addStretch()
        footer = QHBoxLayout()
        self.stop = QPushButton("取消")
        self.stop.clicked.connect(self.cancel_task)
        self.stop.setEnabled(False)
        self.start = QPushButton("开始解压")
        self.start.setObjectName("primary")
        self.start.clicked.connect(self.start_task)
        self.start.setEnabled(False)
        footer.addWidget(self.stop)
        footer.addWidget(self.start, 1)
        actions.addLayout(footer)
        self.splitter.addWidget(left_scroll)
        right = QWidget()
        right.setMinimumWidth(380)
        monitor = QVBoxLayout(right)
        monitor.setContentsMargins(18, 0, 0, 0)
        monitor.setSpacing(10)
        self.status = label("等待选择文件", "status", True)
        monitor.addWidget(self.status)
        self.progress = QProgressBar()
        self.progress.setObjectName("overall")
        self.progress.setAccessibleName("整体运行状态")
        self.progress.setTextVisible(False)
        self.progress.setRange(0, 1)
        self.progress.setValue(0)
        monitor.addWidget(self.progress)
        self.extraction_status = label("解压进度：等待开始", "muted", True)
        monitor.addWidget(self.extraction_status)
        self.extraction_progress = QProgressBar()
        self.extraction_progress.setAccessibleName("当前压缩包解压进度")
        self.extraction_progress.setRange(0, 100)
        self.extraction_progress.setValue(0)
        monitor.addWidget(self.extraction_progress)
        monitor.addWidget(label("运行日志", "section"))
        self.log = QPlainTextEdit()
        self.log.setReadOnly(True)
        self.log.setPlaceholderText("文件、处理阶段和密码验证结果会显示在这里。")
        self.log.setMinimumHeight(300)
        self.log.setMaximumBlockCount(10000)
        monitor.addWidget(self.log, 1)
        locations = QHBoxLayout()
        self.open_button = QPushButton("打开结果目录")
        self.open_button.clicked.connect(self.open_output)
        self.open_button.setEnabled(False)
        self.cache_button = QPushButton("打开任务缓存")
        self.cache_button.clicked.connect(self.open_cache)
        self.cache_button.setVisible(False)
        locations.addWidget(self.open_button)
        locations.addWidget(self.cache_button)
        locations.addStretch()
        monitor.addLayout(locations)
        self.splitter.addWidget(right)
        self.splitter.setChildrenCollapsible(False)
        self.splitter.setSizes([490, 640])
        self.splitter.setStretchFactor(0, 0)
        self.splitter.setStretchFactor(1, 1)
        self.update_fixed()
        self.update_password_count()
        if config_error:
            self.log.appendPlainText("已有配置无法读取，当前使用默认设置；原配置文件尚未修改。")

    def update_fixed(self):
        enabled = self.mode.value().startswith("fixed")
        self.directory.setEnabled(enabled)
        self.browse_directory.setEnabled(enabled)
        self.directory_widget.setVisible(enabled)

    def update_password_count(self):
        self.password_count.setText(f"密码库：{len(self.settings.passwords)} 个密码")

    def choose_directory(self):
        directory = QFileDialog.getExistingDirectory(self, "选择输出目录", self.directory.text())
        if directory:
            self.directory.setText(directory)

    def manage_passwords(self):
        dialog = PasswordDialog(self.settings.passwords, self, self.save_passwords)
        dialog.exec()

    def save_passwords(self, passwords):
        self.settings.passwords = list(passwords)
        self.update_password_count()
        try:
            save_settings(self.settings)
        except (OSError, UnpackError):
            QMessageBox.warning(self, "保存失败", "密码库暂存在本窗口，无法写入本机配置目录。")
            return False
        return True

    def read_settings(self):
        return Settings(
            output_mode=self.mode.value(), fixed_directory=self.directory.text(),
            depth=self.depth.value(), keep_intermediates=self.retention.value(),
            passwords=list(self.settings.passwords),
            filename_encoding=self.filename_encoding.currentData(),
            delete_sources=self.delete_sources.value(),
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
        self.extraction_progress.setValue(0)
        self.extraction_status.setText("解压进度：等待开始")
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
        self.worker.progress.connect(self.on_progress)
        self.worker.ready.connect(self.on_ready)
        self.worker.completed.connect(self.on_completed)
        self.worker.failed.connect(self.on_failed)
        self.worker.finished.connect(self.on_worker_finished)
        self.set_busy(True)
        self.open_button.setEnabled(False)
        self.cache_button.setVisible(False)
        self.extraction_progress.setValue(0)
        self.extraction_status.setText("解压进度：等待解压")
        self.progress.setRange(0, 0)
        self.status.setText("正在识别与处理…")
        self.worker.start()

    def on_ready(self, task):
        self.task = task

    def on_message(self, message):
        self.log.appendPlainText(message)

    def on_progress(self, update: TaskProgress):
        if update.stage == "删除原文件":
            self.stop.setEnabled(False)
        context = f"第 {update.depth} 层 · {update.archive}" if update.depth else ""
        self.status.setText(f"{update.stage} · {context}" if context else update.stage)
        if update.percent is not None:
            self.extraction_progress.setValue(update.percent)
            self.extraction_status.setText(f"当前包解压：{update.percent}%")
        elif update.stage in {"准备处理", "检查压缩包", "检查密码", "兼容复试密码"}:
            self.extraction_progress.setValue(0)
            self.extraction_status.setText("解压进度：等待解压")
        else:
            self.extraction_status.setText("当前包已解压" if self.extraction_progress.value() == 100 else "解压进度：等待解压")

    def on_completed(self, result):
        self.output, self.retry = result.output, False
        self.progress.setRange(0, 1)
        self.progress.setValue(1)
        self.status.setText(f"已完成 · {result.archives} 个压缩包 / {result.layers} 层")
        if result.warnings:
            self.status.setText("解压已完成，原文件清理未完成，请查看日志")
        self.log.appendPlainText(f"最终内容：{result.output}")
        self.open_button.setEnabled(True)
        self.cache_button.setVisible(result.session is not None)
        self.start.setText("再次解压")
        if result.deleted_sources:
            self.paths = []
            self.start.setText("请选择新文件")
            self.start.setEnabled(False)

    def on_failed(self, kind, message):
        self.retry = self.task is not None
        self.progress.setRange(0, 1)
        self.progress.setValue(0)
        self.status.setText({"password": "等待补充密码", "cancel": "已取消", "error": "处理未完成"}[kind])
        self.extraction_status.setText("解压进度：已停止")
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
