"""
KHInsider Downloader Pro - GUI

PyQt6 desktop interface. The layout is a three-zone workspace: a command bar for
the album URL, a left inspector holding the album card / per-format content
sections / output options, and a right work area with the tracklist and a live
parallel-transfer monitor.
"""

import os
import sys
import threading
import time
from typing import Dict, List, Optional

from PyQt6 import QtCore, QtGui, QtWidgets
from PyQt6.QtCore import Qt, QThread, pyqtSignal
from PyQt6.QtGui import QColor, QFont, QIcon, QPixmap

from khinsider_core import (
    LOSSLESS_FORMATS,
    AlbumInfo,
    ArtworkInfo,
    DownloadOptions,
    DownloadStats,
    DownloadTask,
    KHInsiderDownloader,
    KHInsiderScraper,
    TrackInfo,
    format_bytes,
    format_duration,
    format_speed,
    normalize_album_url,
    parse_size_to_bytes,
    sanitize_filename,
)

# --------------------------------------------------------------------------
# Design tokens
#
# Qt stylesheets support no variables, so the palette lives here and is
# interpolated into the sheet once at import time.
# --------------------------------------------------------------------------
C = {
    "bg":            "#0f1117",
    "surface":       "#171a23",
    "surface_alt":   "#1c202b",
    "raised":        "#222735",
    "border":        "#272c3a",
    "border_strong": "#343b4f",
    "text":          "#e6e9f0",
    "text_dim":      "#98a2b8",
    "text_muted":    "#6b7590",
    "accent":        "#6366f1",
    "accent_hi":     "#7c80f7",
    "accent_dim":    "#3a3f7a",
    "success":       "#22c55e",
    "danger":        "#ef4444",
    "warning":       "#f59e0b",
    "gold":          "#eab308",
}

MODERN_STYLE = """
* {{ outline: 0; }}

QWidget {{
    background-color: {bg};
    color: {text};
    font-family: "Segoe UI Variable Text", "Segoe UI", -apple-system, Roboto, sans-serif;
    font-size: 13px;
}}

QLabel {{ background: transparent; color: {text_dim}; }}
QLabel#h1 {{ color: {text}; font-size: 17px; font-weight: 700; }}
QLabel#h2 {{ color: {text}; font-size: 14px; font-weight: 600; }}
QLabel#sectionLabel {{
    color: {text_muted};
    font-size: 11px;
    font-weight: 700;
    padding: 2px 0;
}}
QLabel#albumTitle {{ color: {text}; font-size: 15px; font-weight: 700; }}
QLabel#metaText  {{ color: {text_dim}; font-size: 12px; }}
QLabel#hintText  {{ color: {text_muted}; font-size: 11px; }}
QLabel#statValue {{ color: {text}; font-size: 15px; font-weight: 700; }}
QLabel#statLabel {{ color: {text_muted}; font-size: 10px; font-weight: 600; }}

/* ---- Structural panels ---- */
QFrame#panel {{
    background-color: {surface};
    border: 1px solid {border};
    border-radius: 12px;
}}
QFrame#commandBar {{
    background-color: {surface};
    border: 1px solid {border};
    border-radius: 12px;
}}
QFrame#coverFrame {{
    background-color: {bg};
    border: 1px solid {border};
    border-radius: 10px;
}}
QFrame#divider {{ background-color: {border}; border: none; max-height: 1px; }}

/* ---- Selectable content cards (one per audio format) ---- */
QFrame#contentCard {{
    background-color: {surface_alt};
    border: 1px solid {border};
    border-radius: 10px;
}}
QFrame#contentCard:hover {{ border-color: {border_strong}; }}
QFrame#contentCard[selected="true"] {{
    background-color: {raised};
    border: 1px solid {accent};
}}

QLabel#cardTitle    {{ color: {text}; font-size: 13px; font-weight: 700; }}
QLabel#cardMeta     {{ color: {text_muted}; font-size: 11px; }}
QLabel#cardPath     {{ color: {accent_hi}; font-size: 10px; font-weight: 600; }}
QLabel#badgeLossless {{
    color: {gold}; font-size: 9px; font-weight: 800;
    border: 1px solid {gold}; border-radius: 4px; padding: 1px 5px;
}}
QLabel#badgeLossy {{
    color: {text_muted}; font-size: 9px; font-weight: 800;
    border: 1px solid {border_strong}; border-radius: 4px; padding: 1px 5px;
}}

/* ---- Inputs ---- */
QLineEdit {{
    background-color: {bg};
    border: 1px solid {border_strong};
    border-radius: 8px;
    padding: 9px 12px;
    color: {text};
    selection-background-color: {accent};
}}
QLineEdit:focus {{ border: 1px solid {accent}; background-color: {surface_alt}; }}
QLineEdit::placeholder {{ color: {text_muted}; }}
QLineEdit#urlInput {{ font-size: 14px; padding: 11px 14px; }}
QLineEdit#pathInput {{ font-size: 11px; padding: 7px 10px; }}

QSpinBox {{
    background-color: {bg};
    border: 1px solid {border_strong};
    border-radius: 8px;
    padding: 6px 8px;
    color: {text};
}}
QSpinBox:focus {{ border-color: {accent}; }}

/* ---- Buttons ---- */
QPushButton {{
    background-color: {raised};
    border: 1px solid {border_strong};
    border-radius: 8px;
    padding: 8px 14px;
    color: {text};
    font-weight: 600;
}}
QPushButton:hover  {{ background-color: #2b3142; border-color: #43496040; }}
QPushButton:pressed {{ background-color: #1b2029; }}
QPushButton:disabled {{ background-color: {surface}; color: {text_muted}; border-color: {border}; }}

QPushButton#primary {{
    background-color: {accent};
    border: 1px solid {accent_hi};
    color: #ffffff;
    font-size: 14px;
    font-weight: 700;
    padding: 12px 22px;
}}
QPushButton#primary:hover  {{ background-color: {accent_hi}; }}
QPushButton#primary:pressed {{ background-color: #4f52d4; }}
QPushButton#primary:disabled {{ background-color: #292d3d; color: {text_muted}; border-color: {border}; }}

QPushButton#danger {{
    background-color: transparent;
    border: 1px solid {danger};
    color: {danger};
    font-weight: 700;
    padding: 12px 18px;
}}
QPushButton#danger:hover {{ background-color: rgba(239, 68, 68, 0.12); }}
QPushButton#danger:disabled {{ border-color: {border}; color: {text_muted}; }}

QPushButton#ghost {{
    background-color: transparent;
    border: 1px solid {border_strong};
    color: {text_dim};
    padding: 7px 12px;
    font-size: 12px;
}}
QPushButton#ghost:hover {{ color: {text}; border-color: {accent}; }}

QPushButton#iconBtn {{
    background-color: transparent;
    border: 1px solid {border_strong};
    border-radius: 8px;
    padding: 9px 11px;
    font-size: 14px;
}}
QPushButton#iconBtn:hover {{ border-color: {accent}; }}

/* ---- Checkboxes ---- */
QCheckBox {{ background: transparent; color: {text}; spacing: 9px; font-size: 12px; }}
QCheckBox::indicator {{
    width: 17px; height: 17px;
    border-radius: 5px;
    border: 1px solid {border_strong};
    background-color: {bg};
}}
QCheckBox::indicator:hover {{ border-color: {accent}; }}
QCheckBox::indicator:checked {{ background-color: {accent}; border-color: {accent_hi}; }}
QCheckBox::indicator:disabled {{ background-color: {surface}; border-color: {border}; }}

/* ---- Progress ---- */
QProgressBar {{
    background-color: {bg};
    border: 1px solid {border};
    border-radius: 7px;
    text-align: center;
    color: {text};
    font-size: 11px;
    font-weight: 700;
    min-height: 22px;
}}
QProgressBar::chunk {{
    background: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 {accent}, stop:1 #22d3ee);
    border-radius: 6px;
}}
QProgressBar#slim {{
    min-height: 6px; max-height: 6px;
    border: none; border-radius: 3px;
    background-color: {raised};
}}
QProgressBar#slim::chunk {{ background-color: {accent}; border-radius: 3px; }}

/* ---- Table ---- */
QTableWidget {{
    background-color: {surface};
    alternate-background-color: {surface_alt};
    border: 1px solid {border};
    border-radius: 10px;
    gridline-color: transparent;
    color: {text};
    selection-background-color: {accent_dim};
    selection-color: #ffffff;
}}
QTableWidget::item {{ padding: 5px 6px; border: none; }}
QHeaderView::section {{
    background-color: {bg};
    color: {text_muted};
    padding: 8px 6px;
    border: none;
    border-bottom: 1px solid {border};
    font-weight: 700;
    font-size: 10px;
}}
QTableCornerButton::section {{ background-color: {bg}; border: none; }}

/* ---- Transfer monitor rows ---- */
QFrame#transferRow {{ background-color: transparent; border: none; }}
QLabel#transferName {{ color: {text_dim}; font-size: 11px; }}
QLabel#transferStat {{ color: {text_muted}; font-size: 10px; font-weight: 600; }}

/* ---- Log ---- */
QTextEdit#logView {{
    background-color: {bg};
    border: 1px solid {border};
    border-radius: 8px;
    color: {text_dim};
    font-family: "Cascadia Mono", "Consolas", monospace;
    font-size: 11px;
}}

/* ---- Misc chrome ---- */
QSplitter::handle {{ background-color: transparent; width: 10px; }}
QScrollArea {{ background: transparent; border: none; }}
QScrollBar:vertical {{ background: transparent; width: 10px; margin: 2px; }}
QScrollBar::handle:vertical {{ background: {border_strong}; min-height: 28px; border-radius: 5px; }}
QScrollBar::handle:vertical:hover {{ background: {accent}; }}
QScrollBar:horizontal {{ background: transparent; height: 10px; margin: 2px; }}
QScrollBar::handle:horizontal {{ background: {border_strong}; min-width: 28px; border-radius: 5px; }}
QScrollBar::add-line, QScrollBar::sub-line {{ width: 0; height: 0; }}
QScrollBar::add-page, QScrollBar::sub-page {{ background: transparent; }}

QToolTip {{
    background-color: {raised};
    color: {text};
    border: 1px solid {border_strong};
    border-radius: 6px;
    padding: 5px 8px;
}}
""".format(**C)


# ==========================================================================
# Small reusable widgets
# ==========================================================================

class ContentCard(QtWidgets.QFrame):
    """
    A selectable section for one downloadable content type - an audio format
    (FLAC, MP3, ...), the artwork set, or the extras. Shows what the section
    contains, its total size, and the subfolder it will be written to.
    """

    toggled = pyqtSignal()

    def __init__(self, key: str, title: str, subfolder: str = "", lossless: bool = False):
        super().__init__()
        self.setObjectName("contentCard")
        self.key = key

        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(12, 10, 12, 10)
        layout.setSpacing(5)

        top = QtWidgets.QHBoxLayout()
        top.setSpacing(8)

        self.chk = QtWidgets.QCheckBox()
        self.chk.setChecked(True)
        self.chk.stateChanged.connect(self._on_toggle)

        self.lbl_title = QtWidgets.QLabel(title)
        self.lbl_title.setObjectName("cardTitle")

        top.addWidget(self.chk)
        top.addWidget(self.lbl_title)
        top.addStretch()

        if lossless:
            badge = QtWidgets.QLabel("LOSSLESS")
            badge.setObjectName("badgeLossless")
            top.addWidget(badge)
        elif key not in ("artwork", "extras"):
            badge = QtWidgets.QLabel("LOSSY")
            badge.setObjectName("badgeLossy")
            top.addWidget(badge)

        layout.addLayout(top)

        self.lbl_meta = QtWidgets.QLabel("-")
        self.lbl_meta.setObjectName("cardMeta")

        self.lbl_path = QtWidgets.QLabel(f"{subfolder}/" if subfolder else "album root")
        self.lbl_path.setObjectName("cardPath")

        meta_row = QtWidgets.QHBoxLayout()
        meta_row.setContentsMargins(26, 0, 0, 0)
        meta_row.setSpacing(10)
        meta_row.addWidget(self.lbl_meta)
        meta_row.addStretch()
        meta_row.addWidget(self.lbl_path)
        layout.addLayout(meta_row)

        self._refresh_style()

    def _on_toggle(self):
        self._refresh_style()
        self.toggled.emit()

    def _refresh_style(self):
        self.setProperty("selected", "true" if self.chk.isChecked() else "false")
        self.style().unpolish(self)
        self.style().polish(self)

    def set_meta(self, text: str):
        self.lbl_meta.setText(text)

    def is_checked(self) -> bool:
        return self.chk.isChecked()

    def set_checked(self, value: bool):
        self.chk.setChecked(value)


class TransferRow(QtWidgets.QFrame):
    """One live transfer slot in the parallel download monitor."""

    def __init__(self):
        super().__init__()
        self.setObjectName("transferRow")
        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(0, 2, 0, 2)
        layout.setSpacing(3)

        head = QtWidgets.QHBoxLayout()
        head.setSpacing(8)
        self.lbl_name = QtWidgets.QLabel("Idle")
        self.lbl_name.setObjectName("transferName")
        # The name is the flexible half: it elides, the stats never get squeezed.
        self.lbl_name.setSizePolicy(QtWidgets.QSizePolicy.Policy.Ignored,
                                    QtWidgets.QSizePolicy.Policy.Preferred)

        self.lbl_stat = QtWidgets.QLabel("")
        self.lbl_stat.setObjectName("transferStat")
        self.lbl_stat.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        self.lbl_stat.setMinimumWidth(230)

        head.addWidget(self.lbl_name, stretch=1)
        head.addWidget(self.lbl_stat, stretch=0)
        layout.addLayout(head)

        self.bar = QtWidgets.QProgressBar()
        self.bar.setObjectName("slim")
        self.bar.setTextVisible(False)
        self.bar.setRange(0, 100)
        self.bar.setValue(0)
        layout.addWidget(self.bar)

        self.clear()

    def clear(self):
        self.lbl_name.setText("Idle")
        self.lbl_stat.setText("")
        self.bar.setValue(0)
        self.setVisible(False)

    def show_transfer(self, name: str):
        self.setVisible(True)
        self.lbl_name.setText(self._elide(name))
        self.lbl_stat.setText("starting...")
        self.bar.setValue(0)

    def update_transfer(self, done: int, total: int, speed: float):
        if total > 0:
            self.bar.setValue(int(done / total * 100))
            self.lbl_stat.setText(f"{format_bytes(done)} / {format_bytes(total)}  ·  {format_speed(speed)}")
        else:
            self.lbl_stat.setText(f"{format_bytes(done)}  ·  {format_speed(speed)}")

    @staticmethod
    def _elide(name: str, limit: int = 58) -> str:
        return name if len(name) <= limit else name[: limit - 1] + "…"


def plural(count: int, noun: str) -> str:
    return f"{count} {noun}" if count == 1 else f"{count} {noun}s"


def make_checkmark_icon() -> str:
    """
    Styling QCheckBox::indicator replaces the native tick, so draw one once and
    hand its path back for the stylesheet. Returns "" if it cannot be written,
    in which case the indicator falls back to a plain colour fill.
    """
    import tempfile
    path = os.path.join(tempfile.gettempdir(), "khinsider_check.png")
    try:
        pix = QPixmap(17, 17)
        pix.fill(Qt.GlobalColor.transparent)
        painter = QtGui.QPainter(pix)
        painter.setRenderHint(QtGui.QPainter.RenderHint.Antialiasing)
        pen = QtGui.QPen(QColor("#ffffff"), 2.2)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
        painter.setPen(pen)
        painter.drawPolyline(QtGui.QPolygonF([
            QtCore.QPointF(4.0, 8.8), QtCore.QPointF(7.2, 12.0), QtCore.QPointF(13.0, 5.2),
        ]))
        painter.end()
        return path if pix.save(path, "PNG") else ""
    except Exception:
        return ""


def make_stat(value: str, label: str):
    """A compact value-over-label statistic block for the status bar."""
    box = QtWidgets.QWidget()
    box.setMinimumWidth(92)
    lay = QtWidgets.QVBoxLayout(box)
    lay.setContentsMargins(0, 0, 0, 0)
    lay.setSpacing(1)
    lbl_value = QtWidgets.QLabel(value)
    lbl_value.setObjectName("statValue")
    lbl_label = QtWidgets.QLabel(label)
    lbl_label.setObjectName("statLabel")
    lay.addWidget(lbl_value)
    lay.addWidget(lbl_label)
    return box, lbl_value


def make_panel(title: str = "") -> "tuple[QtWidgets.QFrame, QtWidgets.QVBoxLayout]":
    panel = QtWidgets.QFrame()
    panel.setObjectName("panel")
    layout = QtWidgets.QVBoxLayout(panel)
    layout.setContentsMargins(14, 12, 14, 14)
    layout.setSpacing(10)
    if title:
        lbl = QtWidgets.QLabel(title.upper())
        lbl.setObjectName("sectionLabel")
        layout.addWidget(lbl)
    return panel, layout


# ==========================================================================
# Workers
# ==========================================================================

class FetchAlbumWorker(QThread):
    """Fetches and parses album metadata off the UI thread."""
    finished = pyqtSignal(object)       # AlbumInfo
    error = pyqtSignal(str)
    progress_status = pyqtSignal(str)

    def __init__(self, url: str):
        super().__init__()
        self.url = url
        self.scraper = KHInsiderScraper()

    def run(self):
        try:
            self.progress_status.emit("Fetching album page...")
            album = self.scraper.parse_album(self.url)
            self.finished.emit(album)
        except Exception as e:
            self.error.emit(f"Failed to fetch album: {e}")


class DownloadWorker(QThread):
    """
    Resolves CDN links, then runs the whole queue in parallel through
    KHInsiderDownloader.download_queue.

    All callbacks below fire on pool threads; they only emit Qt signals, which
    Qt delivers to the UI thread as queued events.
    """
    status_changed = pyqtSignal(str)
    queue_built = pyqtSignal(int, int, int, int)        # total, audio, artwork, extras
    stats_updated = pyqtSignal(int, int, int, float, float, float)
    # ^ completed, failed, total, bytes, speed, eta
    transfer_started = pyqtSignal(str, str)             # slot key, display name
    transfer_progress = pyqtSignal(str, int, int, float)  # slot key, done, total, speed
    transfer_ended = pyqtSignal(str)                    # slot key
    track_status = pyqtSignal(str, str, bool)           # track title, format, success
    log_message = pyqtSignal(str, str)
    download_finished = pyqtSignal(bool, str)

    def __init__(self, album: AlbumInfo, selected_tracks: List[TrackInfo], options: DownloadOptions):
        super().__init__()
        self.album = album
        self.selected_tracks = selected_tracks
        self.options = options
        self.downloader = KHInsiderDownloader(options)

    def cancel(self):
        self.downloader.cancel()

    def run(self):
        try:
            wants_audio = bool(self.options.resolve_formats(self.album.formats_available))

            if wants_audio and self.selected_tracks:
                pending = [t for t in self.selected_tracks if not t.download_urls]
                if pending:
                    self.status_changed.emit(f"Resolving download links for {len(pending)} tracks...")
                    self.log_message.emit("INFO", f"Resolving CDN links for {len(pending)} tracks...")

                    def on_resolve(done: int, total: int):
                        self.status_changed.emit(f"Resolving links  {done}/{total}")

                    self.downloader.scraper.resolve_all_tracks_concurrently(
                        pending,
                        max_workers=self.options.max_resolve_workers,
                        progress_callback=on_resolve,
                        should_stop=lambda: self.downloader.is_cancelled,
                    )

            if self.downloader.is_cancelled:
                self.download_finished.emit(False, "Cancelled before downloading.")
                return

            queue = self.downloader.build_download_queue(self.album, self.selected_tracks)
            if not queue:
                self.log_message.emit("WARNING", "Nothing selected to download.")
                self.download_finished.emit(True, "Queue empty - nothing to download.")
                return

            n_audio = sum(1 for t in queue if t.file_type == "audio")
            n_art = sum(1 for t in queue if t.file_type == "artwork")
            n_extra = sum(1 for t in queue if t.file_type == "extra")
            self.queue_built.emit(len(queue), n_audio, n_art, n_extra)
            self.log_message.emit(
                "INFO",
                f"Queued {len(queue)} files - {n_audio} audio, {n_art} artwork, {n_extra} extras "
                f"across {self.options.max_concurrent_downloads} parallel connections."
            )
            self.status_changed.emit("Downloading...")

            # Bounded pool of monitor slots so the UI shows exactly the transfers
            # that are actually in flight.
            free_slots = [f"slot{i}" for i in range(self.options.max_concurrent_downloads)]
            task_slots: Dict[int, str] = {}
            slot_lock = threading.Lock()

            def on_start(task: DownloadTask):
                with slot_lock:
                    slot = free_slots.pop() if free_slots else None
                    if slot:
                        task_slots[id(task)] = slot
                if slot:
                    self.transfer_started.emit(slot, os.path.basename(task.target_path))

            def on_progress(task: DownloadTask, done: int, total: int, speed: float):
                slot = task_slots.get(id(task))
                if slot:
                    self.transfer_progress.emit(slot, done, total, speed)

            def on_done(task: DownloadTask, ok: bool, error: str):
                with slot_lock:
                    slot = task_slots.pop(id(task), None)
                    if slot:
                        free_slots.append(slot)
                if slot:
                    self.transfer_ended.emit(slot)

                name = os.path.basename(task.target_path)
                if task.file_type == "audio":
                    self.track_status.emit(
                        task.metadata.get("track_title", ""),
                        task.metadata.get("format", ""),
                        ok,
                    )
                if ok:
                    if task.metadata.get("skipped"):
                        self.log_message.emit("INFO", f"Already complete, skipped: {name}")
                elif not self.downloader.is_cancelled:
                    self.log_message.emit("ERROR", f"Failed: {name} ({error})")

            def on_stats(stats: DownloadStats):
                self.stats_updated.emit(
                    stats.completed_files, stats.failed_files, stats.total_files,
                    float(stats.bytes_downloaded), stats.speed, stats.eta,
                )

            completed, stats = self.downloader.download_queue(
                queue,
                on_task_start=on_start,
                on_task_progress=on_progress,
                on_task_done=on_done,
                on_stats=on_stats,
            )

            if self.downloader.is_cancelled:
                self.download_finished.emit(False, "Download cancelled. Partial files are kept and will resume.")
                return

            self.status_changed.emit("Writing playlists...")
            self.downloader.generate_playlists(self.album, completed)

            avg = format_speed(stats.bytes_downloaded / stats.elapsed) if stats.elapsed else "-"
            summary = (
                f"Downloaded {stats.completed_files} of {stats.total_files} files "
                f"({format_bytes(stats.bytes_downloaded)}) in {format_duration(stats.elapsed)} "
                f"at {avg} average."
            )
            if stats.failed_files:
                summary += f"\n{stats.failed_files} file(s) failed."
            self.log_message.emit("SUCCESS", summary.replace("\n", " "))
            self.download_finished.emit(True, summary)

        except Exception as e:
            self.log_message.emit("ERROR", f"Fatal error: {e}")
            self.download_finished.emit(False, str(e))


# ==========================================================================
# Main window
# ==========================================================================

class KHInsiderMainWindow(QtWidgets.QMainWindow):

    COL_SEL, COL_CD, COL_NUM, COL_TITLE, COL_DUR, COL_SIZES, COL_STATUS = range(7)

    def __init__(self):
        super().__init__()
        self.setWindowTitle("KHInsider Downloader Pro")
        self.resize(1320, 880)
        self.setMinimumSize(1080, 720)

        icon_path = os.path.join(os.path.dirname(__file__), "icon.ico")
        if os.path.exists(icon_path):
            self.setWindowIcon(QIcon(icon_path))

        self.album_info: Optional[AlbumInfo] = None
        self.fetch_worker: Optional[FetchAlbumWorker] = None
        self.download_worker: Optional[DownloadWorker] = None

        self.format_cards: Dict[str, ContentCard] = {}
        self.transfer_slots: Dict[str, TransferRow] = {}
        self.track_rows: Dict[str, int] = {}
        self.track_done: Dict[str, List[str]] = {}

        self._build_ui()

        sheet = MODERN_STYLE
        check_icon = make_checkmark_icon()
        if check_icon:
            # QSS needs forward slashes even on Windows.
            sheet += (
                "\nQCheckBox::indicator:checked { image: url(%s); }\n"
                % check_icon.replace("\\", "/")
            )
        self.setStyleSheet(sheet)
        self.log("INFO", "Ready. Paste a KHInsider album link to begin.")

    # ---------------------------------------------------------------- UI

    def _build_ui(self):
        central = QtWidgets.QWidget()
        self.setCentralWidget(central)
        root = QtWidgets.QVBoxLayout(central)
        root.setContentsMargins(16, 14, 16, 14)
        root.setSpacing(12)

        root.addWidget(self._build_command_bar())

        splitter = QtWidgets.QSplitter(Qt.Orientation.Horizontal)
        splitter.setChildrenCollapsible(False)
        splitter.addWidget(self._build_inspector())
        splitter.addWidget(self._build_workspace())
        splitter.setStretchFactor(0, 0)
        splitter.setStretchFactor(1, 1)
        splitter.setSizes([380, 940])
        root.addWidget(splitter, stretch=1)

        root.addWidget(self._build_status_bar())

    def _build_command_bar(self) -> QtWidgets.QWidget:
        bar = QtWidgets.QFrame()
        bar.setObjectName("commandBar")
        layout = QtWidgets.QHBoxLayout(bar)
        layout.setContentsMargins(16, 12, 16, 12)
        layout.setSpacing(10)

        title_box = QtWidgets.QVBoxLayout()
        title_box.setSpacing(1)
        lbl = QtWidgets.QLabel("KHInsider Downloader")
        lbl.setObjectName("h1")
        sub = QtWidgets.QLabel("Soundtracks · Lossless audio · Full-resolution scans")
        sub.setObjectName("hintText")
        title_box.addWidget(lbl)
        title_box.addWidget(sub)
        layout.addLayout(title_box)

        sep = QtWidgets.QFrame()
        sep.setFrameShape(QtWidgets.QFrame.Shape.VLine)
        sep.setStyleSheet(f"color: {C['border']};")
        layout.addWidget(sep)

        self.txt_url = QtWidgets.QLineEdit()
        self.txt_url.setObjectName("urlInput")
        self.txt_url.setPlaceholderText("Paste a KHInsider album URL or slug, then press Enter")
        self.txt_url.returnPressed.connect(self.on_fetch_clicked)
        self.txt_url.setClearButtonEnabled(True)
        layout.addWidget(self.txt_url, stretch=1)

        btn_paste = QtWidgets.QPushButton("📋")
        btn_paste.setObjectName("iconBtn")
        btn_paste.setToolTip("Paste from clipboard and load")
        btn_paste.clicked.connect(self.on_paste_clicked)
        layout.addWidget(btn_paste)

        self.btn_fetch = QtWidgets.QPushButton("Load Album")
        self.btn_fetch.setObjectName("primary")
        self.btn_fetch.clicked.connect(self.on_fetch_clicked)
        layout.addWidget(self.btn_fetch)

        return bar

    def _build_inspector(self) -> QtWidgets.QWidget:
        scroll = QtWidgets.QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setMinimumWidth(360)
        scroll.setMaximumWidth(460)

        inner = QtWidgets.QWidget()
        col = QtWidgets.QVBoxLayout(inner)
        col.setContentsMargins(0, 0, 6, 0)
        col.setSpacing(12)

        col.addWidget(self._build_album_panel())
        col.addWidget(self._build_content_panel())
        col.addWidget(self._build_output_panel())
        col.addStretch()

        scroll.setWidget(inner)
        return scroll

    def _build_album_panel(self) -> QtWidgets.QWidget:
        panel, layout = make_panel("Album")

        row = QtWidgets.QHBoxLayout()
        row.setSpacing(14)

        cover_frame = QtWidgets.QFrame()
        cover_frame.setObjectName("coverFrame")
        cover_frame.setFixedSize(112, 112)
        cf_layout = QtWidgets.QVBoxLayout(cover_frame)
        cf_layout.setContentsMargins(1, 1, 1, 1)
        self.lbl_cover = QtWidgets.QLabel("No\nartwork")
        self.lbl_cover.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.lbl_cover.setObjectName("hintText")
        cf_layout.addWidget(self.lbl_cover)
        row.addWidget(cover_frame)

        info = QtWidgets.QVBoxLayout()
        info.setSpacing(3)
        self.lbl_album_title = QtWidgets.QLabel("No album loaded")
        self.lbl_album_title.setObjectName("albumTitle")
        self.lbl_album_title.setWordWrap(True)
        self.lbl_album_meta = QtWidgets.QLabel("Load an album to see its details.")
        self.lbl_album_meta.setObjectName("metaText")
        self.lbl_album_meta.setWordWrap(True)
        info.addWidget(self.lbl_album_title)
        info.addWidget(self.lbl_album_meta)
        info.addStretch()
        row.addLayout(info, stretch=1)

        layout.addLayout(row)
        return panel

    def _build_content_panel(self) -> QtWidgets.QWidget:
        panel, layout = make_panel("Content to download")

        hint = QtWidgets.QLabel(
            "Pick exactly which formats you want. Each one is saved to its own subfolder."
        )
        hint.setObjectName("hintText")
        hint.setWordWrap(True)
        layout.addWidget(hint)

        self.cards_container = QtWidgets.QVBoxLayout()
        self.cards_container.setSpacing(8)
        layout.addLayout(self.cards_container)

        self.lbl_no_formats = QtWidgets.QLabel("Audio formats appear here once an album is loaded.")
        self.lbl_no_formats.setObjectName("hintText")
        self.lbl_no_formats.setWordWrap(True)
        self.cards_container.addWidget(self.lbl_no_formats)

        # Artwork and extras are always available as sections.
        self.card_artwork = ContentCard("artwork", "Artwork & Scans", "Artwork")
        self.card_artwork.set_meta("Covers, booklets, disc art")
        self.card_artwork.toggled.connect(self._update_selection_summary)

        self.card_extras = ContentCard("extras", "Extra Files", "")
        self.card_extras.set_meta("Info text, cue sheets, logs")
        self.card_extras.toggled.connect(self._update_selection_summary)

        self.cards_container.addWidget(self.card_artwork)
        self.cards_container.addWidget(self.card_extras)

        quick = QtWidgets.QHBoxLayout()
        quick.setSpacing(6)
        for text, tip, handler in (
            ("All", "Select every available format", self.on_preset_all),
            ("Lossless only", "Select FLAC/WAV and deselect lossy formats", self.on_preset_lossless),
            ("MP3 only", "Select MP3 only", self.on_preset_mp3),
            ("Artwork only", "Deselect all audio", self.on_preset_art),
        ):
            b = QtWidgets.QPushButton(text)
            b.setObjectName("ghost")
            b.setToolTip(tip)
            b.clicked.connect(handler)
            quick.addWidget(b)
        quick.addStretch()
        layout.addLayout(quick)

        return panel

    def _build_output_panel(self) -> QtWidgets.QWidget:
        panel, layout = make_panel("Output")

        path_row = QtWidgets.QHBoxLayout()
        path_row.setSpacing(6)
        self.txt_output_dir = QtWidgets.QLineEdit(os.path.abspath("downloads"))
        self.txt_output_dir.setObjectName("pathInput")
        # Show the start of the path rather than scrolling to the end.
        self.txt_output_dir.setCursorPosition(0)
        self.txt_output_dir.setToolTip(self.txt_output_dir.text())
        btn_browse = QtWidgets.QPushButton("📁")
        btn_browse.setObjectName("iconBtn")
        btn_browse.setToolTip("Choose output folder")
        btn_browse.clicked.connect(self.on_browse_clicked)
        btn_open = QtWidgets.QPushButton("↗")
        btn_open.setObjectName("iconBtn")
        btn_open.setToolTip("Open output folder")
        btn_open.clicked.connect(self.on_open_folder_clicked)
        path_row.addWidget(self.txt_output_dir, stretch=1)
        path_row.addWidget(btn_browse)
        path_row.addWidget(btn_open)
        layout.addLayout(path_row)

        self.lbl_tree = QtWidgets.QLabel("")
        self.lbl_tree.setObjectName("hintText")
        self.lbl_tree.setWordWrap(True)
        layout.addWidget(self.lbl_tree)

        div = QtWidgets.QFrame()
        div.setObjectName("divider")
        div.setFixedHeight(1)
        layout.addWidget(div)

        self.chk_organize = QtWidgets.QCheckBox("Separate subfolder per format")
        self.chk_organize.setChecked(True)
        self.chk_organize.setToolTip("FLAC/, MP3/ and Artwork/ subfolders inside the album folder")
        self.chk_organize.stateChanged.connect(self._update_selection_summary)

        self.chk_embed = QtWidgets.QCheckBox("Embed cover art into audio tags")
        self.chk_embed.setChecked(True)

        self.chk_playlist = QtWidgets.QCheckBox("Generate .m3u playlists")
        self.chk_playlist.setChecked(True)

        layout.addWidget(self.chk_organize)
        layout.addWidget(self.chk_embed)
        layout.addWidget(self.chk_playlist)

        conc_row = QtWidgets.QHBoxLayout()
        lbl_conc = QtWidgets.QLabel("Parallel connections")
        lbl_conc.setObjectName("metaText")
        self.spn_workers = QtWidgets.QSpinBox()
        self.spn_workers.setRange(1, 16)
        self.spn_workers.setValue(6)
        self.spn_workers.setToolTip(
            "Number of files transferred at once.\nHigher is faster on good links; "
            "lower is gentler on the server."
        )
        self.spn_workers.valueChanged.connect(self._rebuild_transfer_slots)
        conc_row.addWidget(lbl_conc)
        conc_row.addStretch()
        conc_row.addWidget(self.spn_workers)
        layout.addLayout(conc_row)

        return panel

    def _build_workspace(self) -> QtWidgets.QWidget:
        container = QtWidgets.QWidget()
        col = QtWidgets.QVBoxLayout(container)
        col.setContentsMargins(0, 0, 0, 0)
        col.setSpacing(12)

        # --- Tracklist ---
        panel, layout = make_panel()

        head = QtWidgets.QHBoxLayout()
        head.setSpacing(8)
        lbl = QtWidgets.QLabel("TRACKLIST")
        lbl.setObjectName("sectionLabel")
        head.addWidget(lbl)

        self.lbl_selection = QtWidgets.QLabel("")
        self.lbl_selection.setObjectName("hintText")
        head.addWidget(self.lbl_selection)
        head.addStretch()

        self.txt_filter = QtWidgets.QLineEdit()
        self.txt_filter.setPlaceholderText("Filter tracks…")
        self.txt_filter.setClearButtonEnabled(True)
        self.txt_filter.setFixedWidth(220)
        self.txt_filter.textChanged.connect(self.on_filter_changed)
        head.addWidget(self.txt_filter)

        for text, handler in (("Select all", self.on_select_all_clicked),
                              ("Clear", self.on_deselect_all_clicked)):
            b = QtWidgets.QPushButton(text)
            b.setObjectName("ghost")
            b.clicked.connect(handler)
            head.addWidget(b)

        layout.addLayout(head)

        self.tbl_tracks = QtWidgets.QTableWidget(0, 7)
        self.tbl_tracks.setHorizontalHeaderLabels(["", "CD", "#", "TITLE", "TIME", "SIZE", "STATUS"])
        self.tbl_tracks.setAlternatingRowColors(True)
        self.tbl_tracks.setShowGrid(False)
        self.tbl_tracks.verticalHeader().setVisible(False)
        self.tbl_tracks.verticalHeader().setDefaultSectionSize(30)
        self.tbl_tracks.setSelectionBehavior(QtWidgets.QAbstractItemView.SelectionBehavior.SelectRows)
        self.tbl_tracks.setEditTriggers(QtWidgets.QAbstractItemView.EditTrigger.NoEditTriggers)
        self.tbl_tracks.itemChanged.connect(self._on_track_item_changed)

        header = self.tbl_tracks.horizontalHeader()
        header.setSectionResizeMode(self.COL_TITLE, QtWidgets.QHeaderView.ResizeMode.Stretch)
        for c in (self.COL_SEL, self.COL_CD, self.COL_NUM, self.COL_DUR, self.COL_SIZES, self.COL_STATUS):
            header.setSectionResizeMode(c, QtWidgets.QHeaderView.ResizeMode.ResizeToContents)
        layout.addWidget(self.tbl_tracks)

        col.addWidget(panel, stretch=1)

        # --- Transfer monitor ---
        mon_panel, mon_layout = make_panel("Active transfers")
        self.transfers_box = QtWidgets.QVBoxLayout()
        self.transfers_box.setSpacing(2)
        mon_layout.addLayout(self.transfers_box)

        self.lbl_idle = QtWidgets.QLabel("No transfers running.")
        self.lbl_idle.setObjectName("hintText")
        mon_layout.addWidget(self.lbl_idle)

        self.log_view = QtWidgets.QTextEdit()
        self.log_view.setObjectName("logView")
        self.log_view.setReadOnly(True)
        self.log_view.setFixedHeight(110)
        self.log_view.setVisible(False)
        mon_layout.addWidget(self.log_view)

        col.addWidget(mon_panel)
        self._rebuild_transfer_slots()

        return container

    def _build_status_bar(self) -> QtWidgets.QWidget:
        panel = QtWidgets.QFrame()
        panel.setObjectName("panel")
        layout = QtWidgets.QVBoxLayout(panel)
        layout.setContentsMargins(16, 12, 16, 12)
        layout.setSpacing(10)

        top = QtWidgets.QHBoxLayout()
        top.setSpacing(24)

        self.lbl_status = QtWidgets.QLabel("Ready")
        self.lbl_status.setObjectName("h2")
        top.addWidget(self.lbl_status, stretch=1)

        for attr, label in (("stat_files", "FILES"), ("stat_data", "DOWNLOADED"),
                            ("stat_speed", "SPEED"), ("stat_eta", "ETA")):
            box, value_label = make_stat("—", label)
            setattr(self, attr, value_label)
            top.addWidget(box)

        layout.addLayout(top)

        self.bar_overall = QtWidgets.QProgressBar()
        self.bar_overall.setValue(0)
        self.bar_overall.setFormat("Idle")
        layout.addWidget(self.bar_overall)

        actions = QtWidgets.QHBoxLayout()
        actions.setSpacing(10)

        self.btn_start = QtWidgets.QPushButton("Start Download")
        self.btn_start.setObjectName("primary")
        self.btn_start.clicked.connect(self.on_start_download_clicked)

        self.btn_cancel = QtWidgets.QPushButton("Stop")
        self.btn_cancel.setObjectName("danger")
        self.btn_cancel.setEnabled(False)
        self.btn_cancel.clicked.connect(self.on_cancel_clicked)

        self.btn_logs = QtWidgets.QPushButton("Show log")
        self.btn_logs.setObjectName("ghost")
        self.btn_logs.clicked.connect(self.on_toggle_logs_clicked)

        actions.addWidget(self.btn_start, stretch=1)
        actions.addWidget(self.btn_cancel)
        actions.addWidget(self.btn_logs)
        layout.addLayout(actions)

        return panel

    def _rebuild_transfer_slots(self):
        """Keeps one monitor row per parallel connection."""
        for row in self.transfer_slots.values():
            self.transfers_box.removeWidget(row)
            row.deleteLater()
        self.transfer_slots.clear()

        for i in range(self.spn_workers.value()):
            row = TransferRow()
            self.transfers_box.addWidget(row)
            self.transfer_slots[f"slot{i}"] = row

    # ------------------------------------------------------- misc helpers

    def log(self, level: str, message: str):
        colors = {"INFO": C["accent_hi"], "SUCCESS": C["success"],
                  "WARNING": C["warning"], "ERROR": C["danger"]}
        color = colors.get(level, C["text_dim"])
        stamp = time.strftime("%H:%M:%S")
        self.log_view.append(
            f'<span style="color:{C["text_muted"]}">{stamp}</span> '
            f'<b style="color:{color}">{level}</b> {message}'
        )

    def selected_formats(self) -> List[str]:
        return [key for key, card in self.format_cards.items() if card.is_checked()]

    # ------------------------------------------------------------ actions

    def on_paste_clicked(self):
        text = QtWidgets.QApplication.clipboard().text().strip()
        if text:
            self.txt_url.setText(text)
            self.on_fetch_clicked()

    def on_browse_clicked(self):
        chosen = QtWidgets.QFileDialog.getExistingDirectory(
            self, "Select download folder", self.txt_output_dir.text())
        if chosen:
            self.txt_output_dir.setText(chosen)
            self._update_selection_summary()

    def on_open_folder_clicked(self):
        path = self.txt_output_dir.text().strip() or "downloads"
        if self.album_info:
            album_path = os.path.join(path, sanitize_filename(self.album_info.title))
            if os.path.exists(album_path):
                path = album_path
        os.makedirs(path, exist_ok=True)
        if sys.platform == "win32":
            os.startfile(path)
        else:
            QtGui.QDesktopServices.openUrl(QtCore.QUrl.fromLocalFile(path))

    def on_toggle_logs_clicked(self):
        visible = not self.log_view.isVisible()
        self.log_view.setVisible(visible)
        self.btn_logs.setText("Hide log" if visible else "Show log")

    def on_fetch_clicked(self):
        url = self.txt_url.text().strip()
        if not url:
            QtWidgets.QMessageBox.warning(self, "No link", "Paste a KHInsider album link first.")
            return

        normalized = normalize_album_url(url)
        self.txt_url.setText(normalized)
        self.btn_fetch.setEnabled(False)
        self.lbl_status.setText("Loading album…")
        self.log("INFO", f"Loading {normalized}")

        self.fetch_worker = FetchAlbumWorker(normalized)
        self.fetch_worker.finished.connect(self.on_fetch_finished)
        self.fetch_worker.error.connect(self.on_fetch_error)
        self.fetch_worker.progress_status.connect(self.lbl_status.setText)
        self.fetch_worker.start()

    def on_fetch_error(self, err: str):
        self.btn_fetch.setEnabled(True)
        self.lbl_status.setText("Could not load album")
        self.log("ERROR", err)
        QtWidgets.QMessageBox.critical(self, "Load failed", err)

    def on_fetch_finished(self, album: AlbumInfo):
        self.btn_fetch.setEnabled(True)
        self.album_info = album
        self.lbl_status.setText("Album loaded")
        self.log("SUCCESS", f"{album.title} — {len(album.tracks)} tracks, {len(album.artworks)} artwork files")

        self.lbl_album_title.setText(album.title)
        bits = []
        if album.platforms:
            bits.append(" / ".join(album.platforms))
        if album.year:
            bits.append(album.year)
        bits.append(f"{len(album.tracks)} tracks")
        lossless_art = sum(1 for a in album.artworks if a.is_lossless)
        bits.append(f"{len(album.artworks)} artwork ({lossless_art} lossless)")
        self.lbl_album_meta.setText("  ·  ".join(bits))

        if album.thumbnail_url:
            self.load_thumbnail_async(album.thumbnail_url)
        else:
            self.lbl_cover.setText("No\nartwork")

        self._build_format_cards(album)
        self.populate_tracks_table(album.tracks)
        self._update_selection_summary()

    def _build_format_cards(self, album: AlbumInfo):
        """Creates one selectable section per audio format the album offers."""
        for card in self.format_cards.values():
            self.cards_container.removeWidget(card)
            card.deleteLater()
        self.format_cards.clear()

        formats = album.formats_available or []
        self.lbl_no_formats.setVisible(not formats)

        for index, fmt in enumerate(formats):
            total = sum(parse_size_to_bytes(t.sizes.get(fmt, "")) for t in album.tracks)
            count = sum(1 for t in album.tracks if t.sizes.get(fmt))
            lossless = fmt in LOSSLESS_FORMATS

            card = ContentCard(fmt, fmt.upper(), fmt.upper(), lossless=lossless)
            meta = plural(count or len(album.tracks), "track")
            if total:
                meta += f"  ·  {format_bytes(total)}"
            card.set_meta(meta)
            card.toggled.connect(self._update_selection_summary)
            self.cards_container.insertWidget(index, card)
            self.format_cards[fmt] = card

        lossless_art = sum(1 for a in album.artworks if a.is_lossless)
        self.card_artwork.set_meta(
            f"{plural(len(album.artworks), 'file')}  ·  {lossless_art} lossless"
            if album.artworks else "None found")
        self.card_artwork.setEnabled(bool(album.artworks))

        self.card_extras.set_meta(
            plural(len(album.extra_files), "file") if album.extra_files else "None found")
        self.card_extras.setEnabled(bool(album.extra_files))

    def _update_selection_summary(self):
        chosen = self.selected_formats()
        n_tracks = len(self.get_selected_tracks())

        if chosen:
            self.lbl_selection.setText(
                f"· {n_tracks} of {self.tbl_tracks.rowCount()} tracks × {', '.join(f.upper() for f in chosen)}")
        else:
            self.lbl_selection.setText("· no audio selected")

        album_name = sanitize_filename(self.album_info.title) if self.album_info else "<Album>"
        lines = [f"{album_name}/"]
        if self.chk_organize.isChecked():
            for fmt in chosen:
                lines.append(f"   {fmt.upper()}/")
            if self.card_artwork.is_checked() and self.card_artwork.isEnabled():
                lines.append("   Artwork/")
        else:
            if chosen or self.card_artwork.is_checked():
                lines.append("   (all files together)")
        self.lbl_tree.setText("\n".join(lines))

        total = 0
        if self.album_info:
            selected_tracks = self.get_selected_tracks()
            for fmt in chosen:
                total += sum(parse_size_to_bytes(t.sizes.get(fmt, "")) for t in selected_tracks)
        self.btn_start.setText(f"Start Download  ·  ~{format_bytes(total)}" if total else "Start Download")

    def load_thumbnail_async(self, thumb_url: str):
        class ThumbWorker(QThread):
            thumb_ready = pyqtSignal(bytes)

            def run(inner):
                try:
                    r = KHInsiderScraper().session.get(thumb_url, timeout=10)
                    if r.status_code == 200:
                        inner.thumb_ready.emit(r.content)
                except Exception:
                    pass

        self._thumb_worker = ThumbWorker()

        def on_thumb(data: bytes):
            pix = QPixmap()
            if pix.loadFromData(data):
                self.lbl_cover.setPixmap(pix.scaled(
                    108, 108,
                    Qt.AspectRatioMode.KeepAspectRatio,
                    Qt.TransformationMode.SmoothTransformation))

        self._thumb_worker.thumb_ready.connect(on_thumb)
        self._thumb_worker.start()

    # --------------------------------------------------------- tracklist

    def populate_tracks_table(self, tracks: List[TrackInfo]):
        self.tbl_tracks.blockSignals(True)
        self.tbl_tracks.setRowCount(0)
        self.tbl_tracks.setRowCount(len(tracks))
        self.track_rows.clear()
        self.track_done.clear()

        multi_disc = len({t.disc_number for t in tracks}) > 1

        for row, track in enumerate(tracks):
            chk = QtWidgets.QTableWidgetItem()
            chk.setFlags(Qt.ItemFlag.ItemIsUserCheckable | Qt.ItemFlag.ItemIsEnabled)
            chk.setCheckState(Qt.CheckState.Checked)
            self.tbl_tracks.setItem(row, self.COL_SEL, chk)

            cd = QtWidgets.QTableWidgetItem(str(track.disc_number) if multi_disc else "")
            cd.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            self.tbl_tracks.setItem(row, self.COL_CD, cd)

            num = QtWidgets.QTableWidgetItem(str(track.track_number))
            num.setTextAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            num.setForeground(QtGui.QBrush(QColor(C["text_muted"])))
            self.tbl_tracks.setItem(row, self.COL_NUM, num)

            self.tbl_tracks.setItem(row, self.COL_TITLE, QtWidgets.QTableWidgetItem(track.title))

            dur = QtWidgets.QTableWidgetItem(track.duration or "—")
            dur.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            dur.setForeground(QtGui.QBrush(QColor(C["text_dim"])))
            self.tbl_tracks.setItem(row, self.COL_DUR, dur)

            sizes = "  ".join(f"{f.upper()} {s}" for f, s in track.sizes.items()) or "—"
            size_item = QtWidgets.QTableWidgetItem(sizes)
            size_item.setForeground(QtGui.QBrush(QColor(C["text_muted"])))
            self.tbl_tracks.setItem(row, self.COL_SIZES, size_item)

            status = QtWidgets.QTableWidgetItem("")
            status.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            self.tbl_tracks.setItem(row, self.COL_STATUS, status)

            self.track_rows[track.title] = row

        self.tbl_tracks.blockSignals(False)

    def _on_track_item_changed(self, item: QtWidgets.QTableWidgetItem):
        if item.column() == self.COL_SEL:
            self._update_selection_summary()

    def on_filter_changed(self, text: str):
        query = text.strip().lower()
        for row in range(self.tbl_tracks.rowCount()):
            title = self.tbl_tracks.item(row, self.COL_TITLE)
            self.tbl_tracks.setRowHidden(row, bool(query) and not (title and query in title.text().lower()))

    def _set_all_checks(self, state: Qt.CheckState):
        self.tbl_tracks.blockSignals(True)
        for row in range(self.tbl_tracks.rowCount()):
            if not self.tbl_tracks.isRowHidden(row):
                item = self.tbl_tracks.item(row, self.COL_SEL)
                if item:
                    item.setCheckState(state)
        self.tbl_tracks.blockSignals(False)
        self._update_selection_summary()

    def on_select_all_clicked(self):
        self._set_all_checks(Qt.CheckState.Checked)

    def on_deselect_all_clicked(self):
        self._set_all_checks(Qt.CheckState.Unchecked)

    def get_selected_tracks(self) -> List[TrackInfo]:
        if not self.album_info:
            return []
        out = []
        for row in range(self.tbl_tracks.rowCount()):
            item = self.tbl_tracks.item(row, self.COL_SEL)
            if item and item.checkState() == Qt.CheckState.Checked and row < len(self.album_info.tracks):
                out.append(self.album_info.tracks[row])
        return out

    # ----------------------------------------------------------- presets

    def on_preset_all(self):
        for card in self.format_cards.values():
            card.set_checked(True)
        self.card_artwork.set_checked(True)
        self.card_extras.set_checked(True)

    def on_preset_lossless(self):
        for fmt, card in self.format_cards.items():
            card.set_checked(fmt in LOSSLESS_FORMATS)
        if not any(c.is_checked() for c in self.format_cards.values()):
            self.log("WARNING", "This album has no lossless format; selection unchanged.")
            self.on_preset_all()

    def on_preset_mp3(self):
        for fmt, card in self.format_cards.items():
            card.set_checked(fmt == "mp3")

    def on_preset_art(self):
        for card in self.format_cards.values():
            card.set_checked(False)
        self.card_artwork.set_checked(True)

    # ---------------------------------------------------------- download

    def on_start_download_clicked(self):
        if not self.album_info:
            self.on_fetch_clicked()
            return

        chosen_formats = self.selected_formats()
        tracks = self.get_selected_tracks()

        if not chosen_formats and not self.card_artwork.is_checked() and not self.card_extras.is_checked():
            QtWidgets.QMessageBox.warning(
                self, "Nothing selected",
                "Select at least one audio format, the artwork section, or the extras section.")
            return

        if chosen_formats and not tracks:
            QtWidgets.QMessageBox.warning(
                self, "No tracks selected",
                "You picked an audio format but no tracks. Select tracks or deselect the audio formats.")
            return

        options = DownloadOptions(
            output_dir=self.txt_output_dir.text().strip() or "downloads",
            selected_formats=chosen_formats,
            download_artwork=self.card_artwork.is_checked() and self.card_artwork.isEnabled(),
            download_extras=self.card_extras.is_checked() and self.card_extras.isEnabled(),
            organize_by_format=self.chk_organize.isChecked(),
            embed_cover_art=self.chk_embed.isChecked(),
            generate_m3u=self.chk_playlist.isChecked(),
            max_concurrent_downloads=self.spn_workers.value(),
            max_resolve_workers=max(8, self.spn_workers.value() * 2),
        )

        self._set_busy(True)
        self.track_done.clear()
        for row in range(self.tbl_tracks.rowCount()):
            item = self.tbl_tracks.item(row, self.COL_STATUS)
            if item:
                item.setText("")

        self.download_worker = DownloadWorker(self.album_info, tracks, options)
        self.download_worker.status_changed.connect(self.lbl_status.setText)
        self.download_worker.queue_built.connect(self.on_queue_built)
        self.download_worker.stats_updated.connect(self.on_stats_updated)
        self.download_worker.transfer_started.connect(self.on_transfer_started)
        self.download_worker.transfer_progress.connect(self.on_transfer_progress)
        self.download_worker.transfer_ended.connect(self.on_transfer_ended)
        self.download_worker.track_status.connect(self.on_track_status)
        self.download_worker.log_message.connect(self.log)
        self.download_worker.download_finished.connect(self.on_download_finished)
        self.download_worker.start()

    def _set_busy(self, busy: bool):
        self.btn_start.setEnabled(not busy)
        self.btn_cancel.setEnabled(busy)
        self.btn_fetch.setEnabled(not busy)
        self.spn_workers.setEnabled(not busy)
        if busy:
            self.bar_overall.setValue(0)
            self.bar_overall.setFormat("Preparing…")
            self.lbl_idle.setVisible(False)
        else:
            for row in self.transfer_slots.values():
                row.clear()
            self.lbl_idle.setVisible(True)

    def on_cancel_clicked(self):
        if self.download_worker:
            self.lbl_status.setText("Stopping…")
            self.log("WARNING", "Stop requested — partial files are kept for resume.")
            self.download_worker.cancel()
            self.btn_cancel.setEnabled(False)

    def on_queue_built(self, total: int, audio: int, artwork: int, extras: int):
        self.bar_overall.setFormat(f"0 / {total} files")
        self.stat_files.setText(f"0/{total}")

    def on_stats_updated(self, completed: int, failed: int, total: int,
                         data: float, speed: float, eta: float):
        done = completed + failed
        pct = int(done / total * 100) if total else 0
        self.bar_overall.setValue(pct)
        self.bar_overall.setFormat(f"{done} / {total} files  ·  {pct}%")
        self.stat_files.setText(f"{completed}/{total}")
        self.stat_data.setText(format_bytes(data))
        self.stat_speed.setText(format_speed(speed))
        self.stat_eta.setText(format_duration(eta))

    def on_transfer_started(self, slot: str, name: str):
        row = self.transfer_slots.get(slot)
        if row:
            row.show_transfer(name)

    def on_transfer_progress(self, slot: str, done: int, total: int, speed: float):
        row = self.transfer_slots.get(slot)
        if row:
            row.update_transfer(done, total, speed)

    def on_transfer_ended(self, slot: str):
        row = self.transfer_slots.get(slot)
        if row:
            row.clear()

    def on_track_status(self, title: str, fmt: str, success: bool):
        row = self.track_rows.get(title)
        if row is None:
            return
        item = self.tbl_tracks.item(row, self.COL_STATUS)
        if not item:
            return

        if success:
            done = self.track_done.setdefault(title, [])
            if fmt and fmt not in done:
                done.append(fmt)
            item.setText("✓ " + " ".join(f.upper() for f in done))
            item.setForeground(QtGui.QBrush(QColor(C["success"])))
        else:
            item.setText("✕ failed")
            item.setForeground(QtGui.QBrush(QColor(C["danger"])))

    def on_download_finished(self, success: bool, summary: str):
        self._set_busy(False)
        self.stat_speed.setText("—")
        self.stat_eta.setText("—")

        if success:
            self.bar_overall.setValue(100)
            self.bar_overall.setFormat("Complete")
            self.lbl_status.setText("Download complete")

            box = QtWidgets.QMessageBox(self)
            box.setWindowTitle("Download complete")
            box.setIcon(QtWidgets.QMessageBox.Icon.Information)
            box.setText(summary)
            open_btn = box.addButton("Open folder", QtWidgets.QMessageBox.ButtonRole.AcceptRole)
            box.addButton("Close", QtWidgets.QMessageBox.ButtonRole.RejectRole)
            box.exec()
            if box.clickedButton() is open_btn:
                self.on_open_folder_clicked()
        else:
            self.lbl_status.setText("Stopped")
            self.bar_overall.setFormat("Stopped")
            QtWidgets.QMessageBox.warning(self, "Download stopped", summary)

    def closeEvent(self, event: QtGui.QCloseEvent):
        if self.download_worker and self.download_worker.isRunning():
            self.download_worker.cancel()
            self.download_worker.wait(3000)
        event.accept()


def main():
    os.environ.setdefault("QT_AUTO_SCREEN_SCALE_FACTOR", "1")
    app = QtWidgets.QApplication(sys.argv)
    app.setStyle("Fusion")
    app.setApplicationName("KHInsider Downloader Pro")

    window = KHInsiderMainWindow()
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
