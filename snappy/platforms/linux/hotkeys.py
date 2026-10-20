"""Linux: global keyboard shortcuts.

On Wayland, an application cannot listen to the keyboard when it does not have the focus: the
shortcut is handed to the desktop, which runs `snappy --hotkey save`; that command notifies the
Snappy already running (single-instance socket), which saves the clip.

  KDE Plasma           kglobalaccel (D-Bus), active right away and after a restart
  GNOME, Budgie...     custom shortcuts (gsettings)
  X11 (i3, XFCE...)    XGrabKey on the root window, inside Snappy itself
  Others (Hyprland...) the line to add by hand is shown in the settings
"""

from __future__ import annotations

import ast
import re
import ctypes
import ctypes.util
import logging
import os
import select
import shutil
import subprocess
import threading

from PySide6.QtCore import QObject, Signal

from ...i18n import tr
from . import system

log = logging.getLogger(__name__)

_MODS = {"alt": "Alt", "ctrl": "Ctrl", "control": "Ctrl", "shift": "Shift", "maj": "Shift",
         "win": "Super", "meta": "Super", "super": "Super"}
# Named keys -> X11 keysym name (also used by GNOME). French names are accepted too.
_NAMED_KEYS = {
    "space": "space", "espace": "space", "enter": "Return", "return": "Return", "tab": "Tab",
    "esc": "Escape", "escape": "Escape", "backspace": "BackSpace", "insert": "Insert", "ins": "Insert",
    "delete": "Delete", "del": "Delete", "suppr": "Delete", "home": "Home", "end": "End",
    "pgup": "Prior", "pageup": "Prior", "pgdown": "Next", "pagedown": "Next",
    "left": "Left", "up": "Up", "right": "Right", "down": "Down",
    "print": "Print", "printscreen": "Print", "pause": "Pause", "scrolllock": "Scroll_Lock",
    **{f"num{i}": f"KP_{i}" for i in range(10)},
}


def parse_hotkey(text: str) -> tuple[tuple[str, ...], str]:
    """"Alt+F10" -> (("Alt",), "F10"): modifiers and X11 keysym. Raises ValueError if invalid."""
    parts = [p.strip() for p in text.replace(" ", "").split("+") if p.strip()]
    if not parts:
        raise ValueError(tr("Empty shortcut"))
    mods = []
    for p in parts[:-1]:
        m = _MODS.get(p.lower())
        if m is None:
            raise ValueError(tr("Unknown modifier: {key}", key=p))
        if m not in mods:
            mods.append(m)
    key = parts[-1]
    low = key.lower()
    if low in _MODS:
        raise ValueError(tr("The shortcut must end with a key, not a modifier"))
    if len(key) == 1 and key.isalnum():
        sym = low
    elif low.startswith("f") and low[1:].isdigit() and 1 <= int(low[1:]) <= 24:
        sym = f"F{int(low[1:])}"
    elif low in _NAMED_KEYS:
        sym = _NAMED_KEYS[low]
    else:
        raise ValueError(tr("Unknown key: {key}", key=key))
    return tuple(mods), sym


def gnome_binding(text: str) -> str:
    """"Ctrl+Shift+F10" -> "<Control><Shift>F10"."""
    mods, sym = parse_hotkey(text)
    names = {"Alt": "<Alt>", "Ctrl": "<Control>", "Shift": "<Shift>", "Super": "<Super>"}
    return "".join(names[m] for m in mods) + sym


def qt_key_code(text: str) -> int:
    """Qt code of the shortcut (modifiers + key), as KDE stores it."""
    from PySide6.QtGui import QKeySequence

    mods, sym = parse_hotkey(text)
    names = {"Alt": "Alt", "Ctrl": "Ctrl", "Shift": "Shift", "Super": "Meta"}
    qt_keys = {"Return": "Return", "Escape": "Esc", "BackSpace": "Backspace", "Prior": "PgUp", "Next": "PgDown",
               "Scroll_Lock": "ScrollLock", "space": "Space", "Print": "Print"}
    key = qt_keys.get(sym, sym[3:] if sym.startswith("KP_") else sym.upper() if len(sym) == 1 else sym)
    seq = QKeySequence("+".join([names[m] for m in mods] + [key]))
    if seq.isEmpty():
        raise ValueError(tr("Shortcut unknown to Qt: {key}", key=text))
    combo = seq[0]
    return combo.toCombined() if hasattr(combo, "toCombined") else int(combo)


def _desktop() -> str:
    return os.environ.get("XDG_CURRENT_DESKTOP", "").lower()


def _is_x11() -> bool:
    return bool(os.environ.get("DISPLAY")) and not os.environ.get("WAYLAND_DISPLAY")


LABELS = {"save": tr("Save a clip")}


# --------------------------------------------------------------------------
# KDE Plasma: kglobalaccel
# --------------------------------------------------------------------------
class _Kde:
    @staticmethod
    def available() -> bool:
        return any(k in _desktop() for k in ("kde", "plasma")) and bool(shutil.which("gdbus"))

    @staticmethod
    def _call(method: str, *args: str) -> str:
        cmd = ["gdbus", "call", "--session", "--dest", "org.kde.kglobalaccel", "--object-path", "/kglobalaccel",
               "--method", f"org.kde.KGlobalAccel.{method}", *args]
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=5)
        if proc.returncode != 0:
            raise OSError(proc.stderr.strip() or f"kglobalaccel {method}")
        return proc.stdout

    @classmethod
    def bind(cls, name: str, text: str, command: list[str]) -> None:
        from PySide6.QtGui import QKeySequence

        apps = system._xdg("XDG_DATA_HOME", ".local/share") / "applications"
        apps.mkdir(parents=True, exist_ok=True)
        desktop_id = f"{system.DESKTOP_ID}-{name}.desktop"
        label = f"{LABELS.get(name, name)} (Snappy)"
        code = qt_key_code(text)
        # X-KDE-Shortcuts: the key is also in the file, which KDE reads again when it reloads the
        # application shortcuts (otherwise it could go back to an old key).
        content = (f"[Desktop Entry]\nType=Application\nName={label}\nExec={system.desktop_exec(command)}\n"
                   f"Icon={system.icon_path()}\nNoDisplay=true\nStartupNotify=false\n"
                   f"X-KDE-GlobalAccel-CommandShortcut=true\n"
                   f"X-KDE-Shortcuts={QKeySequence(code).toString(QKeySequence.PortableText)}\n")
        path = apps / desktop_id
        try:
            unchanged = path.read_text(encoding="utf-8") == content
        except OSError:
            unchanged = False
        if not unchanged:           # only rewritten when it changes: KDE reloads on every write
            path.write_text(content, encoding="utf-8")
        action = repr([desktop_id, "_launch", label, label])
        cls._call("doRegister", action)
        # SetPresent (2) | NoAutoloading (4): without SetPresent, KDE keeps the shortcut inactive. KDE
        # returns the keys it actually kept: a key already taken elsewhere is refused.
        reply = cls._call("setShortcut", action, repr([code]), "6")
        if code not in [int(n) for n in re.findall(r"-?\d+", reply)]:
            raise ValueError(tr('"{key}" is already used by another KDE shortcut.', key=text))
        # At login, KDE takes the key from kglobalshortcutsrc ([services][snappy-save.desktop]
        # _launch), which wins over X-KDE-Shortcuts: it is written there right away instead of
        # relying on KDE's delayed write, otherwise an old key can come back after a restart.
        writer = shutil.which("kwriteconfig6") or shutil.which("kwriteconfig5")
        if writer:
            subprocess.run([writer, "--file", "kglobalshortcutsrc", "--group", "services", "--group", desktop_id,
                            "--key", "_launch", QKeySequence(code).toString(QKeySequence.PortableText)],
                           capture_output=True, timeout=5)


# --------------------------------------------------------------------------
# GNOME and derivatives: custom shortcuts
# --------------------------------------------------------------------------
class _Gnome:
    SCHEMA = "org.gnome.settings-daemon.plugins.media-keys"
    ROOT = "/org/gnome/settings-daemon/plugins/media-keys/custom-keybindings/"

    @staticmethod
    def available() -> bool:
        return any(k in _desktop() for k in ("gnome", "unity", "budgie", "pantheon")) and bool(shutil.which("gsettings"))

    @classmethod
    def _set(cls, schema: str, key: str, value: str) -> None:
        proc = subprocess.run(["gsettings", "set", schema, key, value], capture_output=True, text=True, timeout=5)
        if proc.returncode != 0:
            raise OSError(proc.stderr.strip() or "gsettings")

    @classmethod
    def bind(cls, name: str, text: str, command: list[str]) -> None:
        path = f"{cls.ROOT}{system.DESKTOP_ID}-{name}/"
        cur = subprocess.run(["gsettings", "get", cls.SCHEMA, "custom-keybindings"], capture_output=True,
                             text=True, timeout=5).stdout.strip()
        paths = [] if not cur or cur.startswith("@") else list(ast.literal_eval(cur))
        if path not in paths:
            cls._set(cls.SCHEMA, "custom-keybindings", str(paths + [path]))
        kb = f"{cls.SCHEMA}.custom-keybinding:{path}"
        cls._set(kb, "name", f"{LABELS.get(name, name)} (Snappy)")
        cls._set(kb, "command", system.shell_command(command))
        cls._set(kb, "binding", gnome_binding(text))
        got = subprocess.run(["gsettings", "get", kb, "binding"], capture_output=True, text=True,
                             timeout=5).stdout.strip().strip("'")
        if got != gnome_binding(text):
            raise OSError(f"GNOME kept {got!r} instead of {gnome_binding(text)!r}")


# --------------------------------------------------------------------------
# X11: XGrabKey inside Snappy
# --------------------------------------------------------------------------
class _XKeyEvent(ctypes.Structure):
    _fields_ = [("type", ctypes.c_int), ("serial", ctypes.c_ulong), ("send_event", ctypes.c_int),
                ("display", ctypes.c_void_p), ("window", ctypes.c_ulong), ("root", ctypes.c_ulong),
                ("subwindow", ctypes.c_ulong), ("time", ctypes.c_ulong), ("x", ctypes.c_int), ("y", ctypes.c_int),
                ("x_root", ctypes.c_int), ("y_root", ctypes.c_int), ("state", ctypes.c_uint),
                ("keycode", ctypes.c_uint), ("same_screen", ctypes.c_int)]


class _XEvent(ctypes.Union):
    _fields_ = [("type", ctypes.c_int), ("xkey", _XKeyEvent), ("pad", ctypes.c_long * 24)]


_X_MASKS = {"Shift": 1, "Ctrl": 4, "Alt": 8, "Super": 64}
_LOCK_MASKS = (0, 2, 16, 2 | 16)       # Caps Lock, Num Lock: the shortcut works whatever their state
KEY_PRESS = 2


class _X11Grabber(threading.Thread):
    """Thread that holds its own X connection and receives the grabbed keys."""

    def __init__(self, on_key):
        super().__init__(name="x11-hotkeys", daemon=True)
        path = ctypes.util.find_library("X11")
        if not path:
            raise OSError("libX11 not found")
        x = self.x = ctypes.CDLL(path)
        x.XOpenDisplay.restype = ctypes.c_void_p
        x.XDefaultRootWindow.restype = ctypes.c_ulong
        x.XDefaultRootWindow.argtypes = [ctypes.c_void_p]
        x.XStringToKeysym.restype = ctypes.c_ulong
        x.XKeysymToKeycode.argtypes = [ctypes.c_void_p, ctypes.c_ulong]
        x.XKeysymToKeycode.restype = ctypes.c_ubyte
        x.XGrabKey.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_uint, ctypes.c_ulong, ctypes.c_int,
                               ctypes.c_int, ctypes.c_int]
        x.XUngrabKey.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_uint, ctypes.c_ulong]
        x.XSync.argtypes = [ctypes.c_void_p, ctypes.c_int]
        x.XPending.argtypes = [ctypes.c_void_p]
        x.XNextEvent.argtypes = [ctypes.c_void_p, ctypes.POINTER(_XEvent)]
        x.XConnectionNumber.argtypes = [ctypes.c_void_p]
        x.XCloseDisplay.argtypes = [ctypes.c_void_p]
        self.dpy = x.XOpenDisplay(None)
        if not self.dpy:
            raise OSError("X11 display unavailable")
        self.root = x.XDefaultRootWindow(self.dpy)
        self.on_key = on_key
        self.grabs: dict[tuple[int, int], str] = {}     # (keycode, mask) -> name
        self._lock = threading.Lock()
        self._error = 0
        # A key already grabbed by another app causes an asynchronous X error: without a handler,
        # Xlib would terminate Snappy.
        handler_type = ctypes.CFUNCTYPE(ctypes.c_int, ctypes.c_void_p, ctypes.c_void_p)
        self._handler = handler_type(self._on_error)
        x.XSetErrorHandler(self._handler)
        self._stopping = False

    def _on_error(self, _dpy, _event) -> int:
        self._error = 1
        return 0

    def grab(self, name: str, text: str) -> None:
        mods, sym = parse_hotkey(text)
        x = self.x
        keycode = x.XKeysymToKeycode(self.dpy, x.XStringToKeysym(sym.encode()))
        if not keycode:
            raise ValueError(tr("Key not on the keyboard: {key}", key=sym))
        mask = sum(_X_MASKS[m] for m in mods)
        with self._lock:
            self.ungrab(name)
            self._error = 0
            for extra in _LOCK_MASKS:
                x.XGrabKey(self.dpy, keycode, mask | extra, self.root, 1, 1, 1)    # GrabModeAsync
            x.XSync(self.dpy, 0)
            if self._error:
                for extra in _LOCK_MASKS:
                    x.XUngrabKey(self.dpy, keycode, mask | extra, self.root)
                x.XSync(self.dpy, 0)
                raise ValueError(tr('"{key}" is already used by another application.', key=text))
            self.grabs[(keycode, mask)] = name

    def ungrab(self, name: str) -> None:
        for (keycode, mask), n in list(self.grabs.items()):
            if n == name:
                for extra in _LOCK_MASKS:
                    self.x.XUngrabKey(self.dpy, keycode, mask | extra, self.root)
                self.x.XSync(self.dpy, 0)
                del self.grabs[(keycode, mask)]

    def run(self) -> None:
        fd = self.x.XConnectionNumber(self.dpy)
        event = _XEvent()
        while not self._stopping:
            select.select([fd], [], [], 0.5)
            with self._lock:
                while self.x.XPending(self.dpy):
                    self.x.XNextEvent(self.dpy, ctypes.byref(event))
                    if event.type == KEY_PRESS:
                        key = (event.xkey.keycode, event.xkey.state & ~(2 | 16) & 0xFF)
                        if key in self.grabs:
                            self.on_key(self.grabs[key])

    def stop(self) -> None:
        self._stopping = True


# --------------------------------------------------------------------------
class GlobalHotkeys(QObject):
    """Same interface as on Windows: register(name, "Alt+F10"), signal triggered(name)."""

    triggered = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._x11: _X11Grabber | None = None

    def register(self, name: str, text: str) -> None:
        """Raises ValueError if the shortcut is invalid or cannot be installed."""
        parse_hotkey(text)
        command = system.launch_args(["--hotkey", name])
        for backend in (_Kde, _Gnome):
            if backend.available():
                try:
                    backend.bind(name, text, command)
                    return
                except ValueError:
                    raise                   # key refused by the desktop: to be shown in the settings
                except (OSError, SyntaxError, subprocess.SubprocessError) as exc:
                    log.warning("Desktop shortcut not installed (%s): %s", backend.__name__, exc)
        if _is_x11():
            try:
                if self._x11 is None:
                    self._x11 = _X11Grabber(self.triggered.emit)
                    self._x11.start()
                self._x11.grab(name, text)
                return
            except OSError as exc:
                log.warning("X11 shortcut unavailable: %s", exc)
        raise ValueError(tr("This desktop does not let Snappy install the shortcut: in its settings, "
                            "bind {key} to the command {command}", key=text, command=system.short_command(command)))

    def receive(self, name: str) -> None:
        """Desktop shortcut received through the single-instance socket (`snappy --hotkey save`)."""
        self.triggered.emit(name)

    def unregister(self, name: str) -> None:
        if self._x11:
            self._x11.ungrab(name)

    def unregister_all(self) -> None:
        # Desktop shortcuts stay in place: they do nothing while Snappy is closed.
        if self._x11:
            self._x11.stop()
            self._x11 = None

