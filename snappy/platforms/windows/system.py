"""Windows: folders, File Explorer, Recycle Bin, start at login, accent color, Steam,
foreground application, confirmation sound."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

from ... import SOURCE_ROOT
from ...i18n import tr

NAME = "windows"
# Interface texts that depend on the system.
TEXT = {
    "os": "Windows",
    "session_start": tr("when Windows starts"),
    "primary_screen": tr("Windows primary screen"),
    "accent": tr("Windows color (Settings › Personalization › Colors)"),
    "accent_follow": tr("the Windows one"),
    "app_example": tr("Executable name, for example \"VALORANT-Win64-Shipping.exe\""),
    "split_placeholder": tr("Add an application by its executable name (Spotify.exe)…"),
    "capture_hint": tr("Automatic fallback to GDI if Desktop Duplication is not available."),
    "manual_launch": tr("shortcut, .exe, run.bat"),
    "trash": tr("Windows Recycle Bin"),
    "reveal": tr("Show in File Explorer"),
}
# Suggested voice chat applications (executable name); the first one is the default choice.
CHAT_APPS = ["Discord.exe", "DiscordPTB.exe", "DiscordCanary.exe", "Vesktop.exe", "TeamSpeak.exe",
             "ts3client_win64.exe", "Mumble.exe", "Slack.exe", "Zoom.exe", "Teams.exe", "ms-teams.exe"]
# Snappy's notifications place themselves on the game screen, without taking the focus.
OVERLAY_TOAST = True
# Manual recording (start / stop): the FFmpeg ring's segments, appended to a file (services/recorder.py).
MANUAL_RECORDING = True


# --------------------------------------------------------------------------
# Folders
# --------------------------------------------------------------------------
def _roaming() -> Path:
    return Path(os.environ.get("APPDATA", Path.home() / "AppData" / "Roaming"))


def _local() -> Path:
    return Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local"))


def data_dir(app: str) -> Path:
    """Configuration (%APPDATA%\\Snappy)."""
    return _roaming() / app


def cache_dir(app: str) -> Path:
    """Rebuildable caches (%LOCALAPPDATA%\\Snappy\\cache)."""
    return _local() / app / "cache"


def buffer_dir(app: str) -> Path:
    """Replay buffer segments (%LOCALAPPDATA%\\Snappy\\buffer)."""
    return _local() / app / "buffer"


def videos_dir() -> Path:
    return Path.home() / "Videos"


# --------------------------------------------------------------------------
# Process setup
# --------------------------------------------------------------------------
def set_app_id() -> None:
    """Correct taskbar icon when Snappy runs through Python."""
    try:
        import ctypes
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("Snappy.Clips")
    except Exception:
        pass


def enable_dpi_awareness() -> None:
    try:
        import ctypes
        ctypes.windll.shcore.SetProcessDpiAwareness(2)
    except Exception:
        pass


# --------------------------------------------------------------------------
# Foreground application (to name / group the clips)
# --------------------------------------------------------------------------
_IGNORED_APPS = {"explorer", "snappy", "python", "pythonw", "searchhost", "shellexperiencehost"}


def foreground_app_info() -> tuple[str, str]:
    """(readable name, executable path) of the active application."""
    try:
        import ctypes
        from ctypes import wintypes

        user32 = ctypes.WinDLL("user32", use_last_error=True)
        k32 = ctypes.WinDLL("kernel32", use_last_error=True)
        user32.GetForegroundWindow.restype = wintypes.HWND
        hwnd = user32.GetForegroundWindow()
        if not hwnd:
            return "", ""
        pid = wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        k32.OpenProcess.restype = wintypes.HANDLE
        h = k32.OpenProcess(0x1000, False, pid.value)  # PROCESS_QUERY_LIMITED_INFORMATION
        exe = ""
        if h:
            buf = ctypes.create_unicode_buffer(1024)
            size = wintypes.DWORD(len(buf))
            if k32.QueryFullProcessImageNameW(h, 0, buf, ctypes.byref(size)):
                exe = buf.value
            k32.CloseHandle(h)
        name = exe_description(exe) if exe else ""
        stem = Path(exe).stem if exe else ""
        if stem.lower() in _IGNORED_APPS:
            return "", ""
        if not name:
            length = user32.GetWindowTextLengthW(hwnd)
            title = ctypes.create_unicode_buffer(length + 1)
            user32.GetWindowTextW(hwnd, title, length + 1)
            name = title.value or stem
        return name.strip()[:60], exe
    except Exception:
        return "", ""


def exe_description(exe: str) -> str:
    """"FileDescription" field of the executable (often the game name)."""
    try:
        import ctypes
        from ctypes import wintypes

        ver = ctypes.WinDLL("version")
        size = ver.GetFileVersionInfoSizeW(exe, None)
        if not size:
            return ""
        data = ctypes.create_string_buffer(size)
        if not ver.GetFileVersionInfoW(exe, 0, size, data):
            return ""
        ptr = ctypes.c_void_p()
        length = wintypes.UINT()
        if not ver.VerQueryValueW(data, "\\VarFileInfo\\Translation", ctypes.byref(ptr), ctypes.byref(length)) \
                or length.value < 4:
            return ""
        lang = ctypes.cast(ptr, ctypes.POINTER(ctypes.c_ushort * 2)).contents
        key = f"\\StringFileInfo\\{lang[0]:04x}{lang[1]:04x}\\FileDescription"
        if ver.VerQueryValueW(data, key, ctypes.byref(ptr), ctypes.byref(length)) and length.value:
            return ctypes.wstring_at(ptr, length.value - 1)
    except Exception:
        pass
    return ""


# --------------------------------------------------------------------------
# File Explorer, Recycle Bin
# --------------------------------------------------------------------------
def reveal(path: Path) -> None:
    """Opens File Explorer with the file selected."""
    subprocess.Popen(["explorer", "/select,", str(Path(path))])


def open_folder(path: Path) -> None:
    path = Path(path)
    path.mkdir(parents=True, exist_ok=True)
    os.startfile(str(path))  # type: ignore[attr-defined]


def send_to_recycle_bin(paths: list[Path]) -> bool:
    """Windows Recycle Bin (recoverable), without a dialog."""
    if not paths:
        return True
    import ctypes
    from ctypes import wintypes

    class SHFILEOPSTRUCTW(ctypes.Structure):
        _fields_ = [("hwnd", wintypes.HWND), ("wFunc", wintypes.UINT), ("pFrom", wintypes.LPCWSTR),
                    ("pTo", wintypes.LPCWSTR), ("fFlags", ctypes.c_ushort), ("fAnyOperationsAborted", wintypes.BOOL),
                    ("hNameMappings", ctypes.c_void_p), ("lpszProgressTitle", wintypes.LPCWSTR)]

    FO_DELETE = 3
    FOF_SILENT, FOF_NOCONFIRMATION, FOF_ALLOWUNDO, FOF_NOERRORUI = 0x4, 0x10, 0x40, 0x400
    files = "\0".join(str(Path(p).resolve()) for p in paths) + "\0\0"
    op = SHFILEOPSTRUCTW(None, FO_DELETE, files, None,
                         FOF_SILENT | FOF_NOCONFIRMATION | FOF_ALLOWUNDO | FOF_NOERRORUI, False, None, None)
    return ctypes.windll.shell32.SHFileOperationW(ctypes.byref(op)) == 0 and not op.fAnyOperationsAborted


# --------------------------------------------------------------------------
# Start at login
# --------------------------------------------------------------------------
def set_autostart(enabled: bool, minimized: bool = True) -> None:
    """Adds/removes Snappy from startup (HKCU\\...\\Run). `minimized`: started at login, it stays
    in the notification area; started by hand, it always opens its window."""
    import winreg

    key_path = r"Software\Microsoft\Windows\CurrentVersion\Run"
    if getattr(sys, "frozen", False):
        command = f'"{sys.executable}"'
    else:
        # At login the current folder is System32, where "-m snappy" cannot find the package:
        # the source folder is added to the path before starting the app.
        pyw = Path(sys.executable).with_name("pythonw.exe")
        code = f"import sys; sys.path.insert(0, r'{SOURCE_ROOT}'); from snappy.app import main; sys.exit(main())"
        command = f'"{pyw if pyw.exists() else sys.executable}" -c "{code}"'
    if minimized:
        command += " --minimized"
    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, key_path, 0, winreg.KEY_SET_VALUE) as key:
        if enabled:
            winreg.SetValueEx(key, "Snappy", 0, winreg.REG_SZ, command)
        else:
            try:
                winreg.DeleteValue(key, "Snappy")
            except FileNotFoundError:
                pass


# --------------------------------------------------------------------------
# Appearance, Steam, sound
# --------------------------------------------------------------------------
def accent_color() -> str:
    """Windows accent color (Settings › Personalization › Colors)."""
    import winreg

    # (key, value, byte order): AABBGGRR for the first two, AARRGGBB for the last one.
    sources = ((r"Software\Microsoft\Windows\CurrentVersion\Explorer\Accent", "AccentColorMenu", "bgr"),
               (r"Software\Microsoft\Windows\DWM", "AccentColor", "bgr"),
               (r"Software\Microsoft\Windows\DWM", "ColorizationColor", "rgb"))
    for path, name, order in sources:
        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, path) as key:
                v, _ = winreg.QueryValueEx(key, name)
        except OSError:
            continue
        r, g, b = (v & 0xFF, (v >> 8) & 0xFF, (v >> 16) & 0xFF) if order == "bgr" else \
            ((v >> 16) & 0xFF, (v >> 8) & 0xFF, v & 0xFF)
        return f"#{r:02x}{g:02x}{b:02x}"
    return "#0078d4"


def steam_value(name: str):
    """Value Steam keeps up to date in the registry (SteamPath, RunningAppID...)."""
    try:
        import winreg

        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\Valve\Steam") as key:
            return winreg.QueryValueEx(key, name)[0]
    except OSError:
        return None


def steam_dirs() -> list[str]:
    """Possible Steam locations, in order of preference."""
    path = steam_value("SteamPath")
    return ([path] if path else []) + [r"C:\Program Files (x86)\Steam"]


def system_sounds() -> list[tuple[str, str, Path]]:
    """System sounds offered for the confirmation: (key, displayed name, WAV file)."""
    media = Path(os.environ.get("WINDIR", r"C:\Windows")) / "Media"
    return [("notify", tr("Windows — Notification"), media / "Windows Notify System Generic.wav"),
            ("ding", tr("Windows — Ding"), media / "Windows Ding.wav"),
            ("success", tr("Windows — Success"), media / "tada.wav")]


def play_wav(path: Path) -> None:
    """Plays a WAV file without waiting (confirmation sound)."""
    import winsound

    winsound.PlaySound(str(path), winsound.SND_FILENAME | winsound.SND_ASYNC | winsound.SND_NODEFAULT)
