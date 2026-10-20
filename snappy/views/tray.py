"""Notification area view: icon, tooltip, menu and notification balloons."""

from __future__ import annotations

from PySide6.QtCore import QObject, Signal
from PySide6.QtGui import QAction
from PySide6.QtWidgets import QMenu, QSystemTrayIcon

from .. import APP_NAME
from ..i18n import tr
from .widgets import app_icon


class TrayView(QObject):
    saveRequested = Signal()
    openRequested = Signal()
    pauseToggled = Signal()
    openFolderRequested = Signal()
    quitRequested = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.tray = QSystemTrayIcon(app_icon(), self)
        self.tray.setToolTip(f"{APP_NAME} — {tr('starting…')}")
        menu = QMenu()

        def action(text: str, signal) -> QAction:
            # Signal hooked up with connect(): passed as "triggered=", PySide would try to call it.
            act = QAction(text, self)
            act.triggered.connect(lambda _checked=False: signal.emit())
            menu.addAction(act)
            return act

        self.act_save = action(tr("Save a clip"), self.saveRequested)
        action(tr("Open Snappy"), self.openRequested)
        self.act_pause = action(tr("Pause the buffer"), self.pauseToggled)
        action(tr("Open the clips folder"), self.openFolderRequested)
        menu.addSeparator()
        action(tr("Quit Snappy"), self.quitRequested)
        self.tray.setContextMenu(menu)
        self._menu = menu
        self.tray.activated.connect(self._on_activated)
        self.tray.messageClicked.connect(self.openRequested)
        self.tray.show()

    def _on_activated(self, reason) -> None:
        if reason in (QSystemTrayIcon.Trigger, QSystemTrayIcon.DoubleClick):
            self.openRequested.emit()

    def set_hotkey(self, hotkey: str) -> None:
        self.act_save.setText(tr("Save a clip ({key})", key=hotkey))

    def set_paused(self, paused: bool) -> None:
        self.act_pause.setText(tr("Resume the buffer") if paused else tr("Pause the buffer"))

    def set_status(self, text: str) -> None:
        self.tray.setToolTip(f"{APP_NAME} — {text}"[:127])

    def notify(self, text: str, kind: str = "info", ms: int = 4000) -> None:
        """Notification balloon; kind: info, warning, error."""
        if kind == "info":
            self.tray.showMessage(APP_NAME, text, app_icon(), ms)
        else:
            icon = QSystemTrayIcon.Warning if kind == "warning" else QSystemTrayIcon.Critical
            self.tray.showMessage(APP_NAME, text, icon, ms)

    def hide(self) -> None:
        self.tray.hide()
