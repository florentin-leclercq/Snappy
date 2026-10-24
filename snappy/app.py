"""Entry point: prepares the process, the log and Qt, ensures a single instance, then hands the
application over to the main controller (`controllers.app`)."""

from __future__ import annotations

import argparse
import logging
import os
import sys
from logging.handlers import RotatingFileHandler

from PySide6.QtCore import QCoreApplication
from PySide6.QtNetwork import QLocalServer, QLocalSocket
from PySide6.QtWidgets import QApplication

from . import APP_NAME, __version__, update
from .models.settings import Settings, app_data_dir
from .platforms import system

log = logging.getLogger("snappy")
# SNAPPY_INSTANCE: second, isolated instance (development, with separate APPDATA / LOCALAPPDATA).
SINGLE_INSTANCE_KEY = "Snappy-single-instance" + os.environ.get("SNAPPY_INSTANCE", "")


def _setup_logging() -> None:
    log_path = app_data_dir() / "snappy.log"
    handler = RotatingFileHandler(log_path, maxBytes=1_000_000, backupCount=2, encoding="utf-8")
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
    root = logging.getLogger()
    root.setLevel(logging.INFO)
    root.addHandler(handler)
    if sys.stderr:
        root.addHandler(logging.StreamHandler())


def _send(message: bytes) -> bool:
    """Sends a message to the Snappy already running ("show", "hotkey:save"). False if there is none."""
    sock = QLocalSocket()
    sock.connectToServer(SINGLE_INSTANCE_KEY)
    if sock.waitForConnected(300):
        sock.write(message)
        sock.waitForBytesWritten(300)
        sock.disconnectFromServer()
        return True
    return False


def _already_running() -> bool:
    """If Snappy is already running, ask it to show itself and stop here."""
    return _send(b"show")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="snappy", description="Snappy: instant replay")
    parser.add_argument("--minimized", action="store_true", help="start in the notification area")
    parser.add_argument("--demo", action="store_true",
                        help="test video/audio sources instead of the screen (development)")
    parser.add_argument("--hotkey", metavar="ACTION",
                        help="triggers a shortcut in the running Snappy (save: save a clip); "
                             "this is the command the desktop shortcuts run on Linux")
    args = parser.parse_args(argv)

    if args.hotkey:
        core = QCoreApplication(sys.argv[:1])   # needed by the local socket
        sent = _send(f"hotkey:{args.hotkey}".encode())
        core.quit()
        if not sent:
            print("Snappy is not running.", file=sys.stderr)
        return 0 if sent else 1

    system.enable_dpi_awareness()
    system.set_app_id()
    _setup_logging()
    log.info("Snappy %s", __version__)

    from .controllers.app import AppController
    from .views import theme
    from .views.widgets import app_icon

    qapp = QApplication(sys.argv[:1])
    qapp.setApplicationName(APP_NAME)
    qapp.setApplicationVersion(__version__)
    qapp.setDesktopFileName("snappy")          # Wayland: window icon and grouping
    qapp.setQuitOnLastWindowClosed(False)
    qapp.setWindowIcon(app_icon())
    theme.apply(qapp, Settings.load().accent)

    if _already_running():
        log.info("Snappy is already running: existing window shown")
        return 0
    # Snappy.exe: an update downloaded last time is installed now (Snappy restarts by itself).
    if update.self_updating() and update.apply_staged(os.getpid(), sys.argv[1:]):
        return 0
    server = QLocalServer()
    QLocalServer.removeServer(SINGLE_INSTANCE_KEY)
    server.listen(SINGLE_INSTANCE_KEY)

    app = AppController(qapp, args.minimized, demo=args.demo)

    def _on_connection():
        conn = server.nextPendingConnection()
        if conn:
            conn.readyRead.connect(lambda c=conn: app.handle_message(bytes(c.readAll())))
    server.newConnection.connect(_on_connection)

    qapp.aboutToQuit.connect(lambda: app.recorder.stop())
    return qapp.exec()


if __name__ == "__main__":
    sys.exit(main())
