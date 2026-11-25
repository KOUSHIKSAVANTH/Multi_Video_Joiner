"""Main window of Multi Video Joiner."""

from __future__ import annotations

import json
import subprocess
import sys
import threading
from concurrent.futures import ThreadPoolExecutor
from itertools import count
from pathlib import Path
from urllib.parse import urlparse

from PySide6.QtCore import QObject, QSettings, QStandardPaths, Qt, QTimer, QUrl, Signal
from PySide6.QtGui import QAction, QColor, QDesktopServices, QKeySequence, QPainter, QShortcut
from PySide6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QFileDialog,
    QFormLayout,
    QFrame,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMenu,
    QMessageBox,
    QPlainTextEdit,
    QProgressBar,
    QPushButton,
    QScrollArea,
    QSplitter,
    QStyle,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from . import APP_ID, APP_NAME, REPO_URL, __version__
from . import shortcuts, theme
from . import timeparse as tp
from .downloader import AccessOptions, fetch_info
from .engine import CANVASES, MODES, TRANSITIONS, Callbacks, ClipItem, JobResult, JobSettings, Pipeline
from .ffmpeg_tools import Cancelled, ffmpeg_version, find_ffmpeg, probe
from .project import PROJECT_SUFFIX, load_project, save_project

VIDEO_FILTER = "Videos (*.mp4 *.mkv *.mov *.webm *.avi *.m4v *.flv *.wmv *.mpg *.mpeg *.ts *.3gp);;All files (*)"
COL_NUM, COL_SOURCE, COL_RANGE, COL_LABEL, COL_LENGTH, COL_STATUS = range(6)
UID_ROLE = Qt.UserRole + 1

STATE_COLORS = {
    "Queued": theme.MUTED,
    "Fetching info…": theme.MUTED,
    "Ready": theme.TEXT,
    "Downloading": theme.ACCENT_HOVER,
    "Processing": theme.ACCENT_2,
    "Done": theme.SUCCESS,
    "Failed": theme.DANGER,
    "Cancelled": theme.WARNING,
}


def default_output_dir() -> str:
    base = QStandardPaths.writableLocation(QStandardPaths.MoviesLocation) or str(Path.home())
    return str(Path(base) / APP_NAME)


def is_frozen() -> bool:
    return bool(getattr(sys, "frozen", False))


# ------------------------------------------------------------------------- signals


class Bridge(QObject):
    """Thread-safe bridge: worker threads emit, the GUI thread receives."""

    status = Signal(str)
    progress = Signal(float)
    log = Signal(str)
    item_state = Signal(int, str)
    finished = Signal(object)
    failed = Signal(str)
    cancelled = Signal()
    info = Signal(int, str, object, str)  # uid, title, duration, error
    ytdlp_updated = Signal(bool, str)


# ------------------------------------------------------------------ safe inputs


class _NoAccidentalWheel:
    """Scrolling the settings panel must not silently change a value under the mouse."""

    def _init_focus(self):
        self.setFocusPolicy(Qt.StrongFocus)

    def wheelEvent(self, event):
        if self.hasFocus():
            super().wheelEvent(event)
        else:
            event.ignore()


class ComboBox(_NoAccidentalWheel, QComboBox):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._init_focus()


class DoubleSpinBox(_NoAccidentalWheel, QDoubleSpinBox):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._init_focus()


# ---------------------------------------------------------------------- queue view


class QueueTree(QTreeWidget):
    """Reorderable queue that also accepts files and links dropped from outside."""

    externalDrop = Signal(list)
    reordered = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setColumnCount(6)
        self.setHeaderLabels(["#", "Source", "Range", "Label", "Length", "Status"])
        self.setRootIsDecorated(False)
        self.setAlternatingRowColors(True)
        self.setUniformRowHeights(True)
        self.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.setDragDropMode(QAbstractItemView.InternalMove)
        self.setDefaultDropAction(Qt.MoveAction)
        self.setAcceptDrops(True)
        self.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.setContextMenuPolicy(Qt.CustomContextMenu)
        self.setTextElideMode(Qt.ElideMiddle)

        header = self.header()
        header.setStretchLastSection(False)
        header.setSectionResizeMode(COL_NUM, QHeaderView.ResizeToContents)
        header.setSectionResizeMode(COL_SOURCE, QHeaderView.Stretch)
        for col, width in ((COL_RANGE, 150), (COL_LABEL, 130), (COL_LENGTH, 70), (COL_STATUS, 110)):
            header.setSectionResizeMode(col, QHeaderView.Interactive)
            self.setColumnWidth(col, width)

    def _is_external(self, event) -> bool:
        return event.mimeData().hasUrls() and event.source() is not self

    def dragEnterEvent(self, event):
        if self._is_external(event):
            event.acceptProposedAction()
        else:
            super().dragEnterEvent(event)

    def dragMoveEvent(self, event):
        if self._is_external(event):
            event.acceptProposedAction()
        else:
            super().dragMoveEvent(event)

    def dropEvent(self, event):
        if self._is_external(event):
            sources = []
            for url in event.mimeData().urls():
                sources.append(url.toLocalFile() if url.isLocalFile() else url.toString())
            event.acceptProposedAction()
            self.externalDrop.emit([s for s in sources if s])
            return
        super().dropEvent(event)
        self.reordered.emit()

    def paintEvent(self, event):
        super().paintEvent(event)
        if self.topLevelItemCount() == 0:
            painter = QPainter(self.viewport())
            painter.setPen(QColor(theme.MUTED))
            font = painter.font()
            font.setPointSizeF(font.pointSizeF() + 1)
            painter.setFont(font)
            painter.drawText(
                self.viewport().rect(),
                Qt.AlignCenter,
                "Your queue is empty\n\n"
                "Paste a link above, press Ctrl+V here to paste many links,\n"
                "or drag video files and links into this area.",
            )


# ------------------------------------------------------------------------- dialogs


class BulkAddDialog(QDialog):
    EXAMPLE = (
        "# One video per line:  source  [time range]  [label]\n"
        "https://www.youtube.com/watch?v=VIDEO_ID\n"
        "https://www.youtube.com/watch?v=VIDEO_ID  1:25-1:30  wide_shot\n"
        "https://www.instagram.com/reel/REEL_ID | 0-7 | close up\n"
        "C:/Videos/intro.mp4 | | intro\n"
    )

    def __init__(self, parent=None, initial_text: str = ""):
        super().__init__(parent)
        self.setWindowTitle("Add many videos")
        self.resize(720, 420)
        layout = QVBoxLayout(self)
        intro = QLabel(
            "Paste one video per line. A time range and a label are optional; "
            "separate parts with spaces or with <b>|</b>."
        )
        intro.setWordWrap(True)
        layout.addWidget(intro)
        self.editor = QPlainTextEdit()
        self.editor.setPlaceholderText(self.EXAMPLE)
        self.editor.setPlainText(initial_text)
        layout.addWidget(self.editor, 1)
        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.button(QDialogButtonBox.Ok).setText("Add to queue")
        buttons.button(QDialogButtonBox.Ok).setObjectName("Accent")
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def text(self) -> str:
        return self.editor.toPlainText()


# --------------------------------------------------------------------- main window


class MainWindow(QMainWindow):
    def __init__(self, restore_session: bool = True):
        super().__init__()
        self.setWindowTitle(APP_NAME)
        self.setWindowIcon(theme.app_icon())
        self.resize(1280, 800)
        self.setMinimumSize(980, 620)
        self.setAcceptDrops(True)

        self.bridge = Bridge()
        self.items: dict[int, ClipItem] = {}
        self._uid = count(1)
        self._row_states: dict[int, str] = {}
        self._editing_guard = False
        self._pool = ThreadPoolExecutor(max_workers=4, thread_name_prefix="info")
        self._cancel = threading.Event()
        self._job_thread: threading.Thread | None = None
        self._job_uids: list[int] = []
        self._settings_store = QSettings(APP_ID, APP_ID)

        self._build_menu()
        self._build_ui()
        self._connect_bridge()

        if restore_session:
            self._restore_session()
        else:
            self.apply_settings(JobSettings(output_dir=default_output_dir()))
        self._update_mode_ui()
        self._update_summary()
        QTimer.singleShot(0, self._check_tools)

    # ------------------------------------------------------------------ building

    def _build_menu(self) -> None:
        bar = self.menuBar()

        file_menu = bar.addMenu("&File")
        self._action(file_menu, "Add video files…", self.browse_files, "Ctrl+O")
        self._action(file_menu, "Add many links…", self.bulk_add, "Ctrl+B")
        file_menu.addSeparator()
        self._action(file_menu, "Open project…", self.open_project, "Ctrl+Shift+O")
        self._action(file_menu, "Save project as…", self.save_project, "Ctrl+S")
        file_menu.addSeparator()
        self._action(file_menu, "Open output folder", self.open_output_folder)
        file_menu.addSeparator()
        self._action(file_menu, "Quit", self.close, "Ctrl+Q")

        tools = bar.addMenu("&Tools")
        self._action(tools, "Update yt-dlp", self.update_ytdlp)
        self._action(tools, "Show / hide log", self.toggle_log, "Ctrl+L")
        self._action(tools, "Reset settings to defaults", self.reset_settings)
        if shortcuts.supported():
            tools.addSeparator()
            label = "Add to Start menu && desktop" if sys.platform == "win32" else "Add to app launcher && desktop"
            self._action(tools, label, self.add_shortcuts)

        help_menu = bar.addMenu("&Help")
        self._action(help_menu, "How to use", self.show_help, "F1")
        self._action(help_menu, "Project page on GitHub", lambda: QDesktopServices.openUrl(QUrl(REPO_URL)))
        self._action(help_menu, f"About {APP_NAME}", self.show_about)

    def _action(self, menu: QMenu, text: str, slot, shortcut: str | None = None) -> QAction:
        action = QAction(text, self)
        if shortcut:
            action.setShortcut(QKeySequence(shortcut))
        action.triggered.connect(slot)
        menu.addAction(action)
        return action

    def _build_ui(self) -> None:
        root = QWidget()
        self.setCentralWidget(root)
        outer = QVBoxLayout(root)
        outer.setContentsMargins(20, 12, 20, 16)
        outer.setSpacing(12)

        # Header
        header = QHBoxLayout()
        header.setSpacing(14)
        logo = QLabel()
        dpr = self.devicePixelRatioF()
        pix = theme.render_icon(int(52 * dpr))
        pix.setDevicePixelRatio(dpr)
        logo.setPixmap(pix)
        logo.setFixedSize(52, 52)
        header.addWidget(logo, 0, Qt.AlignVCenter)
        titles = QVBoxLayout()
        titles.setSpacing(2)
        title = QLabel(APP_NAME)
        title.setObjectName("Title")
        subtitle = QLabel("Download, trim, restyle and join videos from links or files, all on your computer.")
        subtitle.setObjectName("Subtitle")
        titles.addWidget(title)
        titles.addWidget(subtitle)
        header.addLayout(titles, 1)
        self.tools_badge = QLabel("Checking tools…")
        self.tools_badge.setObjectName("Badge")
        header.addWidget(self.tools_badge, 0, Qt.AlignTop)
        outer.addLayout(header)

        splitter = QSplitter(Qt.Horizontal)
        splitter.setChildrenCollapsible(False)
        splitter.setHandleWidth(14)
        splitter.addWidget(self._build_queue_panel())
        splitter.addWidget(self._build_settings_panel())
        splitter.setStretchFactor(0, 1)
        splitter.setStretchFactor(1, 0)
        splitter.setSizes([880, 360])
        self.splitter = splitter

        self.log_view = QPlainTextEdit()
        self.log_view.setObjectName("Log")
        self.log_view.setReadOnly(True)
        self.log_view.setMaximumBlockCount(3000)
        self.log_view.setPlaceholderText("Detailed progress messages appear here.")
        self.log_view.setVisible(False)

        vsplit = QSplitter(Qt.Vertical)
        vsplit.setChildrenCollapsible(False)
        vsplit.addWidget(splitter)
        vsplit.addWidget(self.log_view)
        vsplit.setStretchFactor(0, 1)
        vsplit.setSizes([600, 160])
        outer.addWidget(vsplit, 1)

        outer.addLayout(self._build_run_bar())

    def _build_queue_panel(self) -> QWidget:
        panel = QWidget()
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)

        # Entry row
        entry = QHBoxLayout()
        entry.setSpacing(8)
        self.source_input = QLineEdit()
        self.source_input.setPlaceholderText("Paste a video link (YouTube, Instagram, Vimeo, X…) or a file path")
        self.source_input.setClearButtonEnabled(True)
        browse = QPushButton("Files…")
        browse.setToolTip("Add video files from your computer (Ctrl+O)")
        browse.clicked.connect(self.browse_files)
        self.range_input = QLineEdit()
        self.range_input.setPlaceholderText("Range, e.g. 1:25-1:30")
        self.range_input.setToolTip("Optional. Leave empty to use the whole video.\nExamples: 0-7, 1:25-1:30, 0:05 to 0:12")
        self.range_input.setFixedWidth(170)
        self.label_input = QLineEdit()
        self.label_input.setPlaceholderText("Label (optional)")
        self.label_input.setToolTip("Used as chapter title, clip file name and dataset folder.")
        self.label_input.setFixedWidth(150)
        add = QPushButton("Add")
        add.setObjectName("Accent")
        add.setMinimumWidth(70)
        add.clicked.connect(self.add_from_inputs)
        for widget in (self.source_input, self.range_input, self.label_input):
            widget.returnPressed.connect(self.add_from_inputs)
        entry.addWidget(self.source_input, 1)
        entry.addWidget(browse)
        entry.addWidget(self.range_input)
        entry.addWidget(self.label_input)
        entry.addWidget(add)
        layout.addLayout(entry)
        self._entry_widgets = [self.source_input, browse, self.range_input, self.label_input, add]

        hint = QLabel(
            "Leave the range empty for the whole video. Double-click a range or label to edit it; "
            "drag rows to reorder."
        )
        hint.setObjectName("Hint")
        layout.addWidget(hint)

        # Queue
        self.tree = QueueTree()
        self.tree.externalDrop.connect(self.add_sources)
        self.tree.reordered.connect(self._after_reorder)
        self.tree.itemDoubleClicked.connect(self._on_double_click)
        self.tree.itemChanged.connect(self._on_item_changed)
        self.tree.customContextMenuRequested.connect(self._context_menu)
        self.tree.itemSelectionChanged.connect(self._update_buttons)
        # Redraw rows when an inline edit closes (also when it was cancelled with Esc).
        self.tree.itemDelegate().closeEditor.connect(lambda *_: self._refresh_all())
        QShortcut(QKeySequence.Delete, self.tree, self.remove_selected)
        QShortcut(QKeySequence.Paste, self.tree, self.paste_into_queue)
        layout.addWidget(self.tree, 1)

        # Queue actions
        actions = QHBoxLayout()
        actions.setSpacing(6)
        self.btn_bulk = QPushButton("Add many…")
        self.btn_bulk.setToolTip("Paste a list of links with optional ranges and labels (Ctrl+B)")
        self.btn_bulk.clicked.connect(self.bulk_add)
        self.btn_up = QPushButton("Move up")
        self.btn_up.clicked.connect(lambda: self.move_selected(-1))
        self.btn_down = QPushButton("Move down")
        self.btn_down.clicked.connect(lambda: self.move_selected(1))
        self.btn_dup = QPushButton("Duplicate")
        self.btn_dup.setToolTip("Copy the selected rows, e.g. to cut several ranges from one video")
        self.btn_dup.clicked.connect(self.duplicate_selected)
        self.btn_remove = QPushButton("Remove")
        self.btn_remove.setObjectName("Danger")
        self.btn_remove.clicked.connect(self.remove_selected)
        self.btn_clear = QPushButton("Clear")
        self.btn_clear.setObjectName("Danger")
        self.btn_clear.clicked.connect(self.clear_all)
        for btn in (self.btn_bulk, self.btn_up, self.btn_down, self.btn_dup, self.btn_remove, self.btn_clear):
            actions.addWidget(btn)
        actions.addStretch(1)
        self.summary = QLabel()
        self.summary.setObjectName("Summary")
        actions.addWidget(self.summary)
        layout.addLayout(actions)
        return panel

    def _card(self, title: str) -> tuple[QFrame, QFormLayout]:
        frame = QFrame()
        frame.setObjectName("Card")
        box = QVBoxLayout(frame)
        box.setContentsMargins(14, 12, 14, 14)
        box.setSpacing(10)
        caption = QLabel(title.upper())
        caption.setObjectName("CardTitle")
        box.addWidget(caption)
        form = QFormLayout()
        form.setLabelAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        form.setHorizontalSpacing(12)
        form.setVerticalSpacing(8)
        form.setFieldGrowthPolicy(QFormLayout.AllNonFixedFieldsGrow)
        box.addLayout(form)
        return frame, form

    def _build_settings_panel(self) -> QWidget:
        content = QWidget()
        layout = QVBoxLayout(content)
        layout.setContentsMargins(0, 0, 6, 0)
        layout.setSpacing(12)

        # Output
        card, form = self._card("Output")
        self.mode_combo = ComboBox()
        for key, label in MODES.items():
            self.mode_combo.addItem(label, key)
        self.mode_combo.currentIndexChanged.connect(self._update_mode_ui)
        form.addRow("Mode", self.mode_combo)
        folder_row = QHBoxLayout()
        self.folder_input = QLineEdit()
        self.folder_input.setToolTip("Where finished videos are saved")
        folder_btn = QPushButton("…")
        folder_btn.setObjectName("Icon")
        folder_btn.setToolTip("Choose output folder")
        folder_btn.setFixedWidth(34)
        folder_btn.clicked.connect(self.choose_output)
        folder_row.addWidget(self.folder_input, 1)
        folder_row.addWidget(folder_btn)
        form.addRow("Folder", folder_row)
        self.name_input = QLineEdit()
        self.name_input.setToolTip("File name of the joined video. Existing files are never overwritten.")
        self.name_label = QLabel("File name")
        form.addRow(self.name_label, self.name_input)
        layout.addWidget(card)

        # Video format
        card, form = self._card("Video format")
        self.canvas_combo = ComboBox()
        for key, (label, _w, _h) in CANVASES.items():
            self.canvas_combo.addItem(label, key)
        self.canvas_combo.currentIndexChanged.connect(self._update_mode_ui)
        form.addRow("Canvas", self.canvas_combo)
        self.res_combo = ComboBox()
        for value in (480, 720, 1080, 1440, 2160):
            self.res_combo.addItem(f"{value}p" + ("  (4K)" if value == 2160 else ""), value)
        self.res_combo.setToolTip("Short side of the output frame; also the maximum download quality.")
        form.addRow("Resolution", self.res_combo)
        self.fill_combo = ComboBox()
        self.fill_combo.addItem("Blurred background", "blur")
        self.fill_combo.addItem("Black bars", "bars")
        self.fill_combo.setToolTip("How to fill empty space when a clip's shape differs from the canvas")
        form.addRow("Fill", self.fill_combo)
        self.fps_combo = ComboBox()
        for value in (24, 25, 30, 50, 60):
            self.fps_combo.addItem(f"{value} fps", value)
        form.addRow("Frame rate", self.fps_combo)
        layout.addWidget(card)

        # Join polish
        card, form = self._card("Joining")
        self.join_card = card
        trans_row = QHBoxLayout()
        self.transition_combo = ComboBox()
        for key, (label, _name) in TRANSITIONS.items():
            self.transition_combo.addItem(label, key)
        self.transition_combo.currentIndexChanged.connect(self._update_mode_ui)
        self.transition_spin = DoubleSpinBox()
        self.transition_spin.setRange(0.2, 3.0)
        self.transition_spin.setSingleStep(0.1)
        self.transition_spin.setDecimals(1)
        self.transition_spin.setSuffix(" s")
        self.transition_spin.setFixedWidth(78)
        self.transition_spin.setToolTip("Transition length")
        trans_row.addWidget(self.transition_combo, 1)
        trans_row.addWidget(self.transition_spin)
        form.addRow("Transition", trans_row)
        self.chapters_cb = QCheckBox("Chapter markers + YouTube chapter list")
        self.chapters_cb.setToolTip(
            "Embeds one chapter per clip in the MP4 and writes a *_chapters.txt\n"
            "you can paste straight into a YouTube description."
        )
        form.addRow(self.chapters_cb)
        layout.addWidget(card)

        # Audio + clips
        card, form = self._card("Audio & clips")
        self.loudnorm_cb = QCheckBox("Even out loudness between videos")
        self.loudnorm_cb.setToolTip("EBU R128 loudness normalisation (-16 LUFS) so no clip is much louder than another")
        form.addRow(self.loudnorm_cb)
        self.group_cb = QCheckBox("Sort clips into folders by label")
        self.group_cb.setToolTip("Exported clips go into a sub-folder per label. A manifest.csv lists every clip.")
        form.addRow(self.group_cb)
        layout.addWidget(card)

        # Access
        card, form = self._card("Sign-in & access")
        self.cookie_combo = ComboBox()
        self.cookie_combo.addItem("Automatic (recommended)", "auto")
        self.cookie_combo.addItem("Don't use cookies", "none")
        for browser in ("chrome", "edge", "firefox", "brave", "opera", "vivaldi"):
            self.cookie_combo.addItem(f"Cookies from {browser.title()}", browser)
        self.cookie_combo.addItem("cookies.txt file…", "file")
        self.cookie_combo.setToolTip(
            "For age-restricted, members-only or rate-limited videos, use the cookies\n"
            "of a browser where you are signed in. Only videos you can already watch work."
        )
        self.cookie_combo.currentIndexChanged.connect(self._update_mode_ui)
        form.addRow("Cookies", self.cookie_combo)
        cookie_row = QHBoxLayout()
        self.cookie_input = QLineEdit()
        self.cookie_input.setPlaceholderText("Path to Netscape cookies.txt")
        cookie_btn = QPushButton("…")
        cookie_btn.setObjectName("Icon")
        cookie_btn.setFixedWidth(34)
        cookie_btn.clicked.connect(self.choose_cookie_file)
        cookie_row.addWidget(self.cookie_input, 1)
        cookie_row.addWidget(cookie_btn)
        self.cookie_row_widget = QWidget()
        self.cookie_row_widget.setLayout(cookie_row)
        cookie_row.setContentsMargins(0, 0, 0, 0)
        form.addRow(self.cookie_row_widget)
        layout.addWidget(card)

        layout.addStretch(1)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        scroll.setWidget(content)
        scroll.setMinimumWidth(340)
        scroll.setMaximumWidth(440)
        self._settings_widgets = [
            self.mode_combo, self.folder_input, folder_btn, self.name_input, self.canvas_combo,
            self.res_combo, self.fill_combo, self.fps_combo, self.transition_combo, self.transition_spin,
            self.chapters_cb, self.loudnorm_cb, self.group_cb, self.cookie_combo, self.cookie_input, cookie_btn,
        ]
        return scroll

    def _build_run_bar(self) -> QHBoxLayout:
        bar = QHBoxLayout()
        bar.setSpacing(12)
        status_col = QVBoxLayout()
        status_col.setSpacing(6)
        self.progress = QProgressBar()
        self.progress.setRange(0, 1000)
        self.progress.setValue(0)
        self.progress.setFormat("%p%")
        self.progress.setTextVisible(False)
        self.status_label = QLabel("Ready")
        self.status_label.setObjectName("Status")
        status_col.addWidget(self.progress)
        status_col.addWidget(self.status_label)
        bar.addLayout(status_col, 1)

        self.log_btn = QPushButton("Show log")
        self.log_btn.setObjectName("Flat")
        self.log_btn.clicked.connect(self.toggle_log)
        self.cancel_btn = QPushButton("Cancel")
        self.cancel_btn.setObjectName("Danger")
        self.cancel_btn.setEnabled(False)
        self.cancel_btn.clicked.connect(self.cancel_job)
        self.start_btn = QPushButton("Join videos")
        self.start_btn.setObjectName("Primary")
        self.start_btn.setMinimumWidth(210)
        self.start_btn.setMinimumHeight(44)
        self.start_btn.clicked.connect(self.start_job)
        QShortcut(QKeySequence("Ctrl+Return"), self, self.start_job)
        bar.addWidget(self.log_btn, 0, Qt.AlignVCenter)
        bar.addWidget(self.cancel_btn, 0, Qt.AlignVCenter)
        bar.addWidget(self.start_btn, 0, Qt.AlignVCenter)
        return bar

    def _connect_bridge(self) -> None:
        b = self.bridge
        b.status.connect(self.status_label.setText)
        b.progress.connect(lambda f: self.progress.setValue(int(f * 1000)))
        b.log.connect(self.append_log)
        b.item_state.connect(self._on_item_state)
        b.finished.connect(self._on_finished)
        b.failed.connect(self._on_failed)
        b.cancelled.connect(self._on_cancelled)
        b.info.connect(self._on_info)
        b.ytdlp_updated.connect(self._on_ytdlp_updated)

    # --------------------------------------------------------------- tools check

    def _check_tools(self) -> None:
        try:
            import yt_dlp

            ytv = yt_dlp.version.__version__
        except Exception:
            ytv = "missing"
        if find_ffmpeg():
            short_ffmpeg = ffmpeg_version().split("-")[0]
            self.tools_badge.setText(f"FFmpeg {short_ffmpeg}  ·  yt-dlp {ytv}")
            self.tools_badge.setToolTip(f"Using FFmpeg at {find_ffmpeg()}")
        else:
            self.tools_badge.setText("FFmpeg not found")
            self.tools_badge.setStyleSheet(f"color: {theme.DANGER};")
            QMessageBox.warning(
                self,
                "FFmpeg not found",
                "FFmpeg is required to process videos.\n\n"
                "Install it (https://ffmpeg.org/download.html) and add it to PATH, "
                "or run:  pip install imageio-ffmpeg",
            )

    # ------------------------------------------------------------------ queue ops

    def add_from_inputs(self) -> None:
        source = self.source_input.text().strip().strip('"')
        if not source:
            self.source_input.setFocus()
            return
        try:
            start, end = tp.parse_optional_range(self.range_input.text())
        except ValueError as err:
            QMessageBox.warning(self, "Invalid time range", str(err))
            self.range_input.setFocus()
            return
        if not self._validate_source(source):
            return
        self.add_item(ClipItem(source, start, end, self.label_input.text().strip()))
        self.source_input.clear()
        self.range_input.clear()
        # The label is kept: consecutive clips usually share it (e.g. the same camera angle).
        self.source_input.setFocus()

    def _validate_source(self, source: str, quiet: bool = False) -> bool:
        if tp.is_url(source) or Path(source).expanduser().is_file():
            return True
        if not quiet:
            QMessageBox.warning(
                self, "Not a link or file", f"'{source}' is neither a web link nor an existing file."
            )
        return False

    def add_sources(self, sources: list[str]) -> None:
        skipped = []
        for source in sources:
            path = Path(source)
            if not tp.is_url(source) and path.is_dir():
                for child in sorted(path.iterdir()):
                    if child.suffix.lower() in {".mp4", ".mkv", ".mov", ".webm", ".avi", ".m4v"}:
                        self.add_item(ClipItem(str(child)))
                continue
            if self._validate_source(source, quiet=True):
                self.add_item(ClipItem(source))
            else:
                skipped.append(source)
        if skipped:
            self.status_label.setText(f"Skipped {len(skipped)} item(s) that are not links or video files")

    def browse_files(self) -> None:
        files, _ = QFileDialog.getOpenFileNames(self, "Add video files", "", VIDEO_FILTER)
        for f in files:
            self.add_item(ClipItem(f))

    def bulk_add(self, initial_text: str = "") -> None:
        dialog = BulkAddDialog(self, initial_text if isinstance(initial_text, str) else "")
        if dialog.exec() != QDialog.Accepted:
            return
        self._add_bulk_text(dialog.text())

    def paste_into_queue(self) -> None:
        text = QApplication.clipboard().text()
        if not text.strip():
            return
        lines = [ln for ln in text.splitlines() if ln.strip()]
        if len(lines) == 1 and not tp.is_url(lines[0].split()[0]) and not Path(lines[0].strip('"')).is_file():
            return
        self._add_bulk_text(text)

    def _add_bulk_text(self, text: str) -> None:
        errors, added = [], 0
        for number, line in enumerate(text.splitlines(), start=1):
            try:
                parsed = tp.parse_bulk_line(line)
                if not parsed:
                    continue
                source, range_text, label = parsed
                source = source.strip('"')
                start, end = tp.parse_optional_range(range_text)
                if not self._validate_source(source, quiet=True):
                    raise ValueError(f"'{source}' is not a link or an existing file")
                self.add_item(ClipItem(source, start, end, label))
                added += 1
            except ValueError as err:
                errors.append(f"Line {number}: {err}")
        self.status_label.setText(f"Added {added} item(s)")
        if errors:
            QMessageBox.warning(self, "Some lines were skipped", "\n".join(errors[:20]))

    def add_item(self, item: ClipItem, fetch: bool = True) -> int:
        uid = next(self._uid)
        self.items[uid] = item
        row = QTreeWidgetItem()
        row.setData(0, UID_ROLE, uid)
        row.setFlags(Qt.ItemIsSelectable | Qt.ItemIsEnabled | Qt.ItemIsDragEnabled | Qt.ItemIsEditable)
        style = self.style()
        row.setIcon(COL_SOURCE, style.standardIcon(QStyle.SP_DriveNetIcon if item.is_url else QStyle.SP_FileIcon))
        self._editing_guard = True
        self.tree.addTopLevelItem(row)
        self._editing_guard = False
        self._row_states[uid] = "Queued"
        self._refresh_row(row)
        self._renumber()
        self._update_summary()
        self._update_buttons()
        if fetch and (not item.title or item.source_duration is None):
            self._fetch_info(uid)
        return uid

    def _fetch_info(self, uid: int) -> None:
        item = self.items.get(uid)
        if not item:
            return
        self._set_state(uid, "Fetching info…")
        access = AccessOptions(cookies="none")

        def work():
            try:
                if item.is_url:
                    title, duration = fetch_info(item.source, access)
                else:
                    info = probe(item.source)
                    title, duration = Path(item.source).name, info.duration
                self.bridge.info.emit(uid, title, duration, "")
            except Exception as err:  # noqa: BLE001
                self.bridge.info.emit(uid, "", None, str(err).splitlines()[0][:200] if str(err) else "error")

        self._pool.submit(work)

    def _on_info(self, uid: int, title: str, duration, error: str) -> None:
        item = self.items.get(uid)
        if not item:
            return
        if title:
            item.title = title
        if duration:
            item.source_duration = float(duration)
        row = self._row_for(uid)
        if row:
            if self._row_states.get(uid) == "Fetching info…":
                self._row_states[uid] = "Queued"
            self._refresh_row(row, info_error=error)
        self._update_summary()

    def _row_for(self, uid: int) -> QTreeWidgetItem | None:
        for i in range(self.tree.topLevelItemCount()):
            row = self.tree.topLevelItem(i)
            if row.data(0, UID_ROLE) == uid:
                return row
        return None

    def _refresh_row(self, row: QTreeWidgetItem, info_error: str = "") -> None:
        uid = row.data(0, UID_ROLE)
        item = self.items[uid]
        self._editing_guard = True
        row.setText(COL_SOURCE, item.display_name)
        tooltip = item.source + (f"\n\nCould not read details: {info_error}" if info_error else "")
        row.setToolTip(COL_SOURCE, tooltip)
        row.setText(COL_RANGE, tp.format_range(item.start, item.end))
        row.setText(COL_LABEL, item.label or "—")
        row.setForeground(COL_LABEL, QColor(theme.TEXT if item.label else theme.MUTED))
        row.setForeground(COL_RANGE, QColor(theme.TEXT if item.has_range else theme.MUTED))
        row.setText(COL_LENGTH, tp.format_duration(item.clip_duration))
        row.setTextAlignment(COL_LENGTH, Qt.AlignRight | Qt.AlignVCenter)

        state = self._row_states.get(uid, "Queued")
        warning = ""
        if item.has_range and item.source_duration and item.start >= item.source_duration:
            warning = "Range past end"
        elif item.has_range and item.source_duration and item.end > item.source_duration + 0.5:
            warning = "Range too long"
        if warning and state in ("Queued", "Ready"):
            row.setText(COL_STATUS, warning)
            row.setForeground(COL_STATUS, QColor(theme.WARNING))
            row.setToolTip(COL_STATUS, f"The video is only {tp.format_clock(item.source_duration)} long.")
        else:
            row.setText(COL_STATUS, state)
            row.setForeground(COL_STATUS, QColor(STATE_COLORS.get(state, theme.MUTED)))
            row.setToolTip(COL_STATUS, "")
        self._editing_guard = False

    def _refresh_all(self) -> None:
        for i in range(self.tree.topLevelItemCount()):
            self._refresh_row(self.tree.topLevelItem(i))

    def _set_state(self, uid: int, state: str) -> None:
        self._row_states[uid] = state
        row = self._row_for(uid)
        if row:
            self._refresh_row(row)

    def _renumber(self) -> None:
        self._editing_guard = True
        for i in range(self.tree.topLevelItemCount()):
            row = self.tree.topLevelItem(i)
            row.setText(COL_NUM, str(i + 1))
            row.setForeground(COL_NUM, QColor(theme.MUTED))
        self._editing_guard = False

    def _after_reorder(self) -> None:
        self._renumber()
        self._update_summary()

    def ordered_items(self) -> list[tuple[int, ClipItem]]:
        result = []
        for i in range(self.tree.topLevelItemCount()):
            uid = self.tree.topLevelItem(i).data(0, UID_ROLE)
            result.append((uid, self.items[uid]))
        return result

    def selected_rows(self) -> list[QTreeWidgetItem]:
        rows = self.tree.selectedItems()
        return sorted(rows, key=self.tree.indexOfTopLevelItem)

    def remove_selected(self) -> None:
        if self._busy():
            return
        for row in self.selected_rows():
            uid = row.data(0, UID_ROLE)
            self.tree.takeTopLevelItem(self.tree.indexOfTopLevelItem(row))
            self.items.pop(uid, None)
            self._row_states.pop(uid, None)
        self._after_reorder()
        self._update_buttons()

    def clear_all(self) -> None:
        if self._busy() or not self.items:
            return
        if QMessageBox.question(self, "Clear queue", f"Remove all {len(self.items)} items?") != QMessageBox.Yes:
            return
        self.tree.clear()
        self.items.clear()
        self._row_states.clear()
        self._after_reorder()
        self._update_buttons()

    def move_selected(self, direction: int) -> None:
        rows = self.selected_rows()
        if not rows or self._busy():
            return
        indexes = [self.tree.indexOfTopLevelItem(r) for r in rows]
        if (direction < 0 and indexes[0] == 0) or (direction > 0 and indexes[-1] == self.tree.topLevelItemCount() - 1):
            return
        order = rows if direction < 0 else list(reversed(rows))
        for row in order:
            idx = self.tree.indexOfTopLevelItem(row)
            self.tree.takeTopLevelItem(idx)
            self.tree.insertTopLevelItem(idx + direction, row)
        self.tree.clearSelection()
        for row in rows:
            row.setSelected(True)
        self._after_reorder()

    def duplicate_selected(self) -> None:
        if self._busy():
            return
        for row in self.selected_rows():
            original = self.items[row.data(0, UID_ROLE)]
            self.add_item(ClipItem.from_dict(original.to_dict()), fetch=False)

    def _update_buttons(self) -> None:
        busy = self._busy()
        has_sel = bool(self.tree.selectedItems())
        self.btn_up.setEnabled(has_sel and not busy)
        self.btn_down.setEnabled(has_sel and not busy)
        self.btn_dup.setEnabled(has_sel and not busy)
        self.btn_remove.setEnabled(has_sel and not busy)
        self.btn_clear.setEnabled(bool(self.items) and not busy)
        self.btn_bulk.setEnabled(not busy)

    def _update_summary(self) -> None:
        items = [it for _uid, it in self.ordered_items()]
        if not items:
            self.summary.setText("")
            return
        lengths = [it.clip_duration for it in items]
        known = [x for x in lengths if x]
        total = sum(known)
        settings = self.collect_settings()
        if settings.mode in ("join", "both") and settings.transition != "none" and len(known) > 1:
            total -= settings.transition_duration * (len(known) - 1)
        prefix = "≥ " if len(known) < len(lengths) else ""
        noun = "video" if len(items) == 1 else "videos"
        self.summary.setText(f"{len(items)} {noun}  ·  {prefix}{tp.format_duration(max(total, 0))} total")

    # ------------------------------------------------------------ editing in place

    def _on_double_click(self, row: QTreeWidgetItem, column: int) -> None:
        if self._busy():
            return
        if column in (COL_RANGE, COL_LABEL):
            item = self.items[row.data(0, UID_ROLE)]
            self._editing_guard = True
            if column == COL_RANGE:
                row.setText(COL_RANGE, f"{tp.format_clock(item.start)}-{tp.format_clock(item.end)}" if item.has_range else "")
            else:
                row.setText(COL_LABEL, item.label)
            self._editing_guard = False
            self.tree.editItem(row, column)
        elif column == COL_SOURCE:
            self.open_source(row)

    def _on_item_changed(self, row: QTreeWidgetItem, column: int) -> None:
        if self._editing_guard:
            return
        uid = row.data(0, UID_ROLE)
        item = self.items.get(uid)
        if not item:
            return
        text = row.text(column).strip()
        if column == COL_RANGE:
            if text in ("Full video", "—"):
                text = ""
            try:
                item.start, item.end = tp.parse_optional_range(text)
            except ValueError as err:
                QMessageBox.warning(self, "Invalid time range", str(err))
        elif column == COL_LABEL:
            item.label = "" if text == "—" else text
        self._refresh_row(row)
        self._update_summary()

    def _context_menu(self, pos) -> None:
        row = self.tree.itemAt(pos)
        if not row:
            return
        menu = QMenu(self)
        menu.addAction("Open source", lambda: self.open_source(row))
        menu.addAction("Edit range", lambda: self._on_double_click(row, COL_RANGE))
        menu.addAction("Edit label", lambda: self._on_double_click(row, COL_LABEL))
        menu.addAction("Refresh details", lambda: self._fetch_info(row.data(0, UID_ROLE)))
        menu.addSeparator()
        menu.addAction("Duplicate", self.duplicate_selected)
        menu.addAction("Remove", self.remove_selected)
        menu.exec(self.tree.viewport().mapToGlobal(pos))

    def open_source(self, row: QTreeWidgetItem) -> None:
        item = self.items[row.data(0, UID_ROLE)]
        if item.is_url:
            url = item.source
            host = urlparse(url).netloc.lower()
            if item.has_range and ("youtube.com" in host or "youtu.be" in host):
                url += ("&" if "?" in url else "?") + f"t={int(item.start)}s"
            QDesktopServices.openUrl(QUrl(url))
        else:
            QDesktopServices.openUrl(QUrl.fromLocalFile(item.source))

    # ------------------------------------------------------------------- settings

    def collect_settings(self) -> JobSettings:
        return JobSettings(
            mode=self.mode_combo.currentData(),
            output_dir=self.folder_input.text().strip() or default_output_dir(),
            output_name=self.name_input.text().strip() or "joined_video",
            canvas=self.canvas_combo.currentData(),
            resolution=int(self.res_combo.currentData()),
            fill=self.fill_combo.currentData(),
            fps=int(self.fps_combo.currentData()),
            transition=self.transition_combo.currentData(),
            transition_duration=float(self.transition_spin.value()),
            loudnorm=self.loudnorm_cb.isChecked(),
            chapters=self.chapters_cb.isChecked(),
            group_by_label=self.group_cb.isChecked(),
            cookies=self.cookie_combo.currentData(),
            cookie_file=self.cookie_input.text().strip(),
        )

    def apply_settings(self, s: JobSettings) -> None:
        def select(combo: QComboBox, value) -> None:
            index = combo.findData(value)
            if index >= 0:
                combo.setCurrentIndex(index)

        select(self.mode_combo, s.mode)
        self.folder_input.setText(s.output_dir or default_output_dir())
        self.folder_input.setCursorPosition(0)
        self.name_input.setText(s.output_name)
        select(self.canvas_combo, s.canvas)
        select(self.res_combo, s.resolution)
        select(self.fill_combo, s.fill)
        select(self.fps_combo, s.fps)
        select(self.transition_combo, s.transition)
        self.transition_spin.setValue(s.transition_duration)
        self.loudnorm_cb.setChecked(s.loudnorm)
        self.chapters_cb.setChecked(s.chapters)
        self.group_cb.setChecked(s.group_by_label)
        select(self.cookie_combo, s.cookies)
        self.cookie_input.setText(s.cookie_file)
        self._update_mode_ui()

    def reset_settings(self) -> None:
        self.apply_settings(JobSettings(output_dir=default_output_dir()))

    def _update_mode_ui(self) -> None:
        if not hasattr(self, "start_btn"):
            return
        mode = self.mode_combo.currentData()
        joining = mode in ("join", "both")
        exporting = mode in ("clips", "both")
        busy = self._busy()
        self.start_btn.setText({"join": "Join videos", "clips": "Export clips", "both": "Export + join"}[mode])
        self.name_input.setEnabled(joining and not busy)
        self.name_label.setEnabled(joining)
        self.join_card.setEnabled(joining and not busy)
        self.transition_spin.setEnabled(joining and not busy and self.transition_combo.currentData() != "none")
        self.group_cb.setEnabled(exporting and not busy)
        self.fill_combo.setEnabled(self.canvas_combo.currentData() != "source" and not busy)
        self.cookie_row_widget.setVisible(self.cookie_combo.currentData() == "file")
        self._update_summary()

    def choose_output(self) -> None:
        folder = QFileDialog.getExistingDirectory(self, "Choose output folder", self.folder_input.text())
        if folder:
            self.folder_input.setText(folder)
            self.folder_input.setCursorPosition(0)

    def choose_cookie_file(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "Select cookies.txt", "", "Text files (*.txt);;All files (*)")
        if path:
            self.cookie_input.setText(path)

    def open_output_folder(self) -> None:
        folder = Path(self.folder_input.text().strip() or default_output_dir())
        folder.mkdir(parents=True, exist_ok=True)
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(folder)))

    # -------------------------------------------------------------------- projects

    def save_project(self) -> None:
        path, _ = QFileDialog.getSaveFileName(
            self, "Save project", str(Path.home() / f"my_project{PROJECT_SUFFIX}"), f"Project (*{PROJECT_SUFFIX})"
        )
        if not path:
            return
        saved = save_project(path, [it for _u, it in self.ordered_items()], self.collect_settings())
        self.status_label.setText(f"Project saved to {saved}")

    def open_project(self) -> None:
        if self._busy():
            return
        path, _ = QFileDialog.getOpenFileName(self, "Open project", str(Path.home()), f"Project (*{PROJECT_SUFFIX});;All files (*)")
        if path:
            self.load_project_file(path)

    def load_project_file(self, path: str) -> None:
        try:
            items, settings = load_project(path)
        except Exception as err:  # noqa: BLE001
            QMessageBox.critical(self, "Could not open project", str(err))
            return
        self.tree.clear()
        self.items.clear()
        self._row_states.clear()
        for item in items:
            self.add_item(item)
        self.apply_settings(settings)
        self.status_label.setText(f"Opened {Path(path).name} · {len(items)} item(s)")

    # ---------------------------------------------------------------------- running

    def _busy(self) -> bool:
        return self._job_thread is not None and self._job_thread.is_alive()

    def _set_running(self, running: bool) -> None:
        for widget in self._entry_widgets + self._settings_widgets:
            widget.setEnabled(not running)
        self.tree.setDragDropMode(QAbstractItemView.NoDragDrop if running else QAbstractItemView.InternalMove)
        self.start_btn.setEnabled(not running)
        self.cancel_btn.setEnabled(running)
        self._update_buttons()
        if not running:
            self._update_mode_ui()

    def start_job(self) -> None:
        if self._busy():
            return
        ordered = self.ordered_items()
        if not ordered:
            QMessageBox.information(self, "Nothing to do", "Add at least one video link or file to the queue.")
            return
        if not find_ffmpeg():
            self._check_tools()
            return
        settings = self.collect_settings()
        if settings.cookies == "file" and not Path(settings.cookie_file).is_file():
            QMessageBox.warning(self, "cookies.txt not found", "Choose a valid cookies.txt file or another cookie option.")
            return

        self._job_uids = [uid for uid, _ in ordered]
        items = [ClipItem.from_dict(item.to_dict()) for _uid, item in ordered]
        for uid in self._job_uids:
            self._set_state(uid, "Queued")
        self._cancel = threading.Event()
        self.progress.setValue(0)
        self.log_view.appendPlainText("")
        self.append_log(f"=== Starting: {MODES[settings.mode]} · {len(items)} item(s) ===")

        bridge = self.bridge
        uids = list(self._job_uids)
        callbacks = Callbacks(
            status=bridge.status.emit,
            progress=bridge.progress.emit,
            log=bridge.log.emit,
            item_state=lambda index, state: bridge.item_state.emit(uids[index], state),
        )
        pipeline = Pipeline(items, settings, callbacks, self._cancel)

        def work():
            try:
                bridge.finished.emit(pipeline.run())
            except Cancelled:
                bridge.cancelled.emit()
            except Exception as err:  # noqa: BLE001
                bridge.failed.emit(str(err) or err.__class__.__name__)

        self._job_thread = threading.Thread(target=work, daemon=True, name="job")
        self._job_thread.start()
        self._set_running(True)
        self.status_label.setText("Starting…")

    def cancel_job(self) -> None:
        if self._busy():
            self._cancel.set()
            self.cancel_btn.setEnabled(False)
            self.status_label.setText("Cancelling…")

    def _on_item_state(self, uid: int, state: str) -> None:
        self._set_state(uid, state)

    def _finish_states(self, state: str) -> None:
        for uid in self._job_uids:
            if self._row_states.get(uid) not in ("Done",):
                self._set_state(uid, state)

    def _wait_thread(self) -> None:
        if self._job_thread:
            self._job_thread.join(timeout=2)
        self._job_thread = None

    def _on_finished(self, result: JobResult) -> None:
        self._wait_thread()
        self._set_running(False)
        self.progress.setValue(1000)
        self.append_log("=== Finished ===")
        parts = []
        if result.joined:
            parts.append(f"Joined video ({tp.format_duration(result.total_duration)}):\n{result.joined}")
        if result.clips:
            parts.append(f"{len(result.clips)} clip(s) exported to:\n{Path(self.collect_settings().output_dir)}")
        if result.chapters_file:
            parts.append(f"YouTube chapter list:\n{result.chapters_file.name}")
        self.status_label.setText("Finished · " + (result.joined.name if result.joined else f"{len(result.clips)} clips"))
        QApplication.alert(self)

        box = QMessageBox(self)
        box.setWindowTitle("All done")
        box.setIcon(QMessageBox.Information)
        box.setText("Your videos are ready.")
        box.setInformativeText("\n\n".join(parts))
        open_btn = box.addButton("Open folder", QMessageBox.ActionRole)
        play_btn = box.addButton("Play video", QMessageBox.ActionRole) if result.joined else None
        box.addButton(QMessageBox.Close)
        box.exec()
        clicked = box.clickedButton()
        if clicked is open_btn:
            target = result.joined.parent if result.joined else Path(self.collect_settings().output_dir)
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(target)))
        elif play_btn is not None and clicked is play_btn:
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(result.joined)))

    def _on_failed(self, message: str) -> None:
        self._wait_thread()
        for uid in self._job_uids:
            state = self._row_states.get(uid)
            if state in ("Downloading", "Processing"):
                self._set_state(uid, "Failed")
            elif state not in ("Done", "Failed"):
                self._set_state(uid, "Queued")
        self._set_running(False)
        self.progress.setValue(0)
        self.status_label.setText("Failed - see the log for details")
        self.append_log(f"ERROR: {message}")
        QMessageBox.critical(self, "Something went wrong", message)

    def _on_cancelled(self) -> None:
        self._wait_thread()
        self._finish_states("Cancelled")
        self._set_running(False)
        self.progress.setValue(0)
        self.status_label.setText("Cancelled")
        self.append_log("=== Cancelled ===")

    # ------------------------------------------------------------------------ misc

    def append_log(self, message: str) -> None:
        self.log_view.appendPlainText(message)

    def toggle_log(self) -> None:
        visible = not self.log_view.isVisible()
        self.log_view.setVisible(visible)
        self.log_btn.setText("Hide log" if visible else "Show log")

    def update_ytdlp(self) -> None:
        if is_frozen():
            box = QMessageBox(self)
            box.setWindowTitle("Update yt-dlp")
            box.setText(
                "This standalone build has yt-dlp built in.\n\n"
                "Download the latest release of Multi Video Joiner to get the newest yt-dlp."
            )
            get = box.addButton("Open releases page", QMessageBox.AcceptRole)
            box.addButton(QMessageBox.Close)
            box.exec()
            if box.clickedButton() is get:
                QDesktopServices.openUrl(QUrl(REPO_URL + "/releases/latest"))
            return
        self.status_label.setText("Updating yt-dlp…")
        self.append_log("Updating yt-dlp with pip…")

        def work():
            try:
                result = subprocess.run(
                    [sys.executable, "-m", "pip", "install", "-U", "yt-dlp"],
                    capture_output=True, text=True,
                    creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0,
                )
                ok = result.returncode == 0
                self.bridge.ytdlp_updated.emit(ok, (result.stdout if ok else result.stderr)[-1500:])
            except Exception as err:  # noqa: BLE001
                self.bridge.ytdlp_updated.emit(False, str(err))

        threading.Thread(target=work, daemon=True).start()

    def _on_ytdlp_updated(self, ok: bool, output: str) -> None:
        self.append_log(output.strip())
        if ok:
            self.status_label.setText("yt-dlp updated - restart the app to use the new version")
            QMessageBox.information(self, "yt-dlp updated", "yt-dlp was updated. Restart the app to use the new version.")
        else:
            self.status_label.setText("yt-dlp update failed")
            QMessageBox.warning(self, "Update failed", output or "pip could not update yt-dlp.")

    def add_shortcuts(self) -> None:
        try:
            created = shortcuts.create_shortcuts(desktop=True)
        except Exception as err:  # noqa: BLE001
            QMessageBox.warning(self, "Could not create shortcuts", str(err))
            return
        where = "Start menu" if sys.platform == "win32" else "app launcher"
        QMessageBox.information(
            self,
            "Shortcuts added",
            f"{APP_NAME} is now in your {where} and on your desktop.\n\n" + "\n".join(str(p) for p in created),
        )

    def show_help(self) -> None:
        QMessageBox.information(
            self,
            "How to use",
            "<b>1. Add videos</b> - paste a link and press Enter, click <i>Files…</i>, drag files or links "
            "onto the queue, or press Ctrl+V on the queue to paste many links at once.<br><br>"
            "<b>2. Trim (optional)</b> - give a range like <code>1:25-1:30</code> to take only that part. "
            "For links, only that part is downloaded.<br><br>"
            "<b>3. Label (optional)</b> - labels become chapter titles, clip file names and dataset folders.<br><br>"
            "<b>4. Choose a mode</b> - join everything into one video, export each clip separately "
            "(with a manifest.csv), or both.<br><br>"
            "<b>5. Pick a format</b> - 16:9, 9:16 for Shorts/Reels, square or 4:5, with a blurred "
            "background or black bars, plus transitions between clips.<br><br>"
            "<b>Shortcuts</b>: Ctrl+Enter start · Ctrl+B add many · Ctrl+O add files · Delete remove · "
            "Ctrl+S save project · Ctrl+L log",
        )

    def show_about(self) -> None:
        try:
            import yt_dlp

            ytv = yt_dlp.version.__version__
        except Exception:
            ytv = "missing"
        QMessageBox.about(
            self,
            f"About {APP_NAME}",
            f"<h3>{APP_NAME} {__version__}</h3>"
            "<p>Download, trim, restyle and join videos from links or local files.</p>"
            f"<p>yt-dlp {ytv}<br>FFmpeg {ffmpeg_version()}</p>"
            f'<p><a href="{REPO_URL}">{REPO_URL}</a></p>'
            "<p>Only download content you have the right to use.</p>",
        )

    # -------------------------------------------------------------- persistence

    def _restore_session(self) -> None:
        store = self._settings_store
        try:
            data = json.loads(store.value("settings", "{}") or "{}")
            settings = JobSettings.from_dict(data) if data else JobSettings()
        except (ValueError, TypeError):
            settings = JobSettings()
        if not settings.output_dir:
            settings.output_dir = default_output_dir()
        self.apply_settings(settings)
        try:
            for entry in json.loads(store.value("queue", "[]") or "[]"):
                if entry.get("source"):
                    self.add_item(ClipItem.from_dict(entry), fetch=False)
            for uid, item in list(self.items.items()):
                if not item.title or item.source_duration is None:
                    self._fetch_info(uid)
        except (ValueError, TypeError):
            pass
        geometry = store.value("geometry")
        if geometry:
            self.restoreGeometry(geometry)

    def _save_session(self) -> None:
        store = self._settings_store
        store.setValue("settings", json.dumps(self.collect_settings().to_dict()))
        store.setValue("queue", json.dumps([it.to_dict() for _u, it in self.ordered_items()]))
        store.setValue("geometry", self.saveGeometry())

    def closeEvent(self, event) -> None:
        if self._busy():
            answer = QMessageBox.question(self, "Job running", "A job is still running. Cancel it and quit?")
            if answer != QMessageBox.Yes:
                event.ignore()
                return
            self._cancel.set()
            self._job_thread.join(timeout=5)
        self._save_session()
        self._pool.shutdown(wait=False, cancel_futures=True)
        event.accept()
