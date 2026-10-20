"""Windows: global keyboard shortcut (RegisterHotKey + Qt native event filter)."""

from __future__ import annotations

from PySide6.QtCore import QAbstractNativeEventFilter, QCoreApplication, QObject, Signal

from ...i18n import tr

# --------------------------------------------------------------------------
# "Ctrl+Shift+F10" -> (modifiers, virtual key code)
# --------------------------------------------------------------------------
MOD_ALT, MOD_CONTROL, MOD_SHIFT, MOD_WIN, MOD_NOREPEAT = 0x1, 0x2, 0x4, 0x8, 0x4000

# French keyboard names are accepted too (maj, espace, suppr).
_MODS = {"alt": MOD_ALT, "ctrl": MOD_CONTROL, "control": MOD_CONTROL,
         "shift": MOD_SHIFT, "maj": MOD_SHIFT, "win": MOD_WIN, "meta": MOD_WIN}

_NAMED_KEYS = {
    "space": 0x20, "espace": 0x20, "enter": 0x0D, "return": 0x0D, "tab": 0x09,
    "esc": 0x1B, "escape": 0x1B, "backspace": 0x08, "insert": 0x2D, "ins": 0x2D,
    "delete": 0x2E, "del": 0x2E, "suppr": 0x2E, "home": 0x24, "end": 0x23,
    "pgup": 0x21, "pageup": 0x21, "pgdown": 0x22, "pagedown": 0x22,
    "left": 0x25, "up": 0x26, "right": 0x27, "down": 0x28,
    "print": 0x2C, "printscreen": 0x2C, "pause": 0x13, "scrolllock": 0x91,
    "num0": 0x60, "num1": 0x61, "num2": 0x62, "num3": 0x63, "num4": 0x64,
    "num5": 0x65, "num6": 0x66, "num7": 0x67, "num8": 0x68, "num9": 0x69,
}


def parse_hotkey(text: str) -> tuple[int, int]:
    """Parses "Alt+F10". Raises ValueError if invalid."""
    parts = [p.strip() for p in text.replace(" ", "").split("+") if p.strip()]
    if not parts:
        raise ValueError(tr("Empty shortcut"))
    mods = 0
    for p in parts[:-1]:
        m = _MODS.get(p.lower())
        if m is None:
            raise ValueError(tr("Unknown modifier: {key}", key=p))
        mods |= m
    key = parts[-1].lower()
    if key in _MODS:
        raise ValueError(tr("The shortcut must end with a key, not a modifier"))
    if len(key) == 1 and key.isalnum():
        vk = ord(key.upper())
    elif key.startswith("f") and key[1:].isdigit() and 1 <= int(key[1:]) <= 24:
        vk = 0x70 + int(key[1:]) - 1
    elif key in _NAMED_KEYS:
        vk = _NAMED_KEYS[key]
    else:
        raise ValueError(tr("Unknown key: {key}", key=parts[-1]))
    return mods | MOD_NOREPEAT, vk


# --------------------------------------------------------------------------
# Global shortcuts
# --------------------------------------------------------------------------
WM_HOTKEY = 0x0312


class _Filter(QAbstractNativeEventFilter):
    def __init__(self, callback):
        super().__init__()
        self.callback = callback

    def nativeEventFilter(self, event_type, message):
        kind = event_type.data() if hasattr(event_type, "data") else bytes(event_type)
        if kind in (b"windows_generic_MSG", b"windows_dispatcher_MSG"):
            from ctypes import wintypes
            msg = wintypes.MSG.from_address(int(message))
            if msg.message == WM_HOTKEY:
                self.callback(int(msg.wParam))
                return True, 0
        return False, 0


class GlobalHotkeys(QObject):
    """Registers global shortcuts; emits `triggered(name)`."""

    triggered = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._ids: dict[int, str] = {}
        self._next_id = 0xB000
        self._filter = _Filter(self._on_hotkey)
        QCoreApplication.instance().installNativeEventFilter(self._filter)

    def register(self, name: str, text: str) -> None:
        """Raises ValueError if the shortcut is invalid or already taken by another app."""
        import ctypes

        mods, vk = parse_hotkey(text)
        self.unregister(name)
        hid = self._next_id
        self._next_id += 1
        if not ctypes.windll.user32.RegisterHotKey(None, hid, mods, vk):
            raise ValueError(tr('"{key}" is already used by another application.', key=text))
        self._ids[hid] = name

    def unregister(self, name: str) -> None:
        import ctypes

        for hid, n in list(self._ids.items()):
            if n == name:
                ctypes.windll.user32.UnregisterHotKey(None, hid)
                del self._ids[hid]

    def unregister_all(self) -> None:
        for name in set(self._ids.values()):
            self.unregister(name)

    def _on_hotkey(self, hid: int) -> None:
        name = self._ids.get(hid)
        if name:
            self.triggered.emit(name)
