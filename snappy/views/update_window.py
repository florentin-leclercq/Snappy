"""Update window: shown while Snappy updates itself (snappy/update.py), with what is being done and
a progress bar. The same window on Windows (Snappy.exe installing itself) and on Linux / from the
sources (the launcher's update step)."""

from __future__ import annotations

import os
import queue
import sys

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import QApplication, QHBoxLayout, QLabel, QProgressBar, QVBoxLayout, QWidget

from .. import APP_NAME
from ..i18n import tr


class UpdateWindow(QWidget):
    """Small window, in front, that cannot be closed: it goes away with the update."""

    def __init__(self):
        super().__init__(None, Qt.Window | Qt.WindowTitleHint | Qt.CustomizeWindowHint | Qt.WindowStaysOnTopHint)
        from .widgets import app_icon

        self.setObjectName("root")
        self.setWindowTitle(APP_NAME)
        self.setWindowIcon(app_icon())
        self.setFixedWidth(440)
        v = QVBoxLayout(self)
        v.setContentsMargins(24, 22, 24, 22)
        v.setSpacing(10)
        head = QHBoxLayout()
        head.setSpacing(12)
        icon = QLabel()
        icon.setPixmap(app_icon(40).pixmap(40, 40))
        head.addWidget(icon)
        title = QLabel(tr("Updating Snappy"))
        title.setObjectName("cardTitle")
        head.addWidget(title, 1)
        v.addLayout(head)
        self.status = QLabel()
        self.status.setWordWrap(True)
        v.addWidget(self.status)
        self.bar = QProgressBar()
        self.bar.setTextVisible(False)
        self.bar.setFixedHeight(6)
        v.addWidget(self.bar)
        hint = QLabel(tr("Snappy starts again by itself once it is done."))
        hint.setObjectName("faint")
        v.addWidget(hint)

    def set_progress(self, text: str, fraction: float | None) -> None:
        """fraction: 0-1, or None for a step whose length is unknown (moving bar)."""
        self.status.setText(text)
        if fraction is None:
            self.bar.setRange(0, 0)
        else:
            self.bar.setRange(0, 1000)
            self.bar.setValue(int(max(0.0, min(1.0, fraction)) * 1000))

    def closeEvent(self, event) -> None:
        event.ignore()          # the update must not be interrupted halfway


def can_show() -> bool:
    """A window can be shown: always on Windows; elsewhere, with a graphical session."""
    return sys.platform == "win32" or bool(os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY")
                                            or os.environ.get("QT_QPA_PLATFORM"))


def show_progress(first: tuple[str, float | None], messages: queue.Queue) -> bool:
    """Shows the update window with `first`, then each (text, fraction) of `messages` until None
    arrives. False when no window can be shown here (the caller then writes to the terminal)."""
    if not can_show():
        return False
    from ..models.settings import Settings
    from . import theme

    app = QApplication.instance() or QApplication(sys.argv[:1])
    app.setQuitOnLastWindowClosed(False)
    try:
        theme.apply(app, Settings.load().accent)
    except Exception:
        theme.apply(app)
    window = UpdateWindow()
    window.set_progress(*first)
    window.show()
    window.raise_()

    def poll():
        try:
            while True:
                item = messages.get_nowait()
                if item is None:
                    timer.stop()
                    window.hide()
                    app.exit(0)
                    return
                window.set_progress(*item)
        except queue.Empty:
            pass

    timer = QTimer(window, interval=50, timeout=poll)
    timer.start()
    app.exec()
    window.deleteLater()
    return True
