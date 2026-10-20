"""Linux: XDG folders, file manager, trash, start at login, accent color, Steam, foreground
application, confirmation sound."""

from __future__ import annotations

import atexit
import configparser
import functools
import glob
import json
import os
import shlex
import shutil
import subprocess
import sys
from pathlib import Path
from urllib.parse import quote

from ... import APP_NAME, SOURCE_ROOT
from ...i18n import tr

NAME = "linux"
TEXT = {
    "os": "Linux",
    "session_start": tr("at login"),
    "primary_screen": tr("primary screen"),
    "accent": tr("Desktop color (KDE, GNOME)"),
    "accent_follow": tr("the desktop's (KDE, GNOME)"),
    "app_example": tr("Application name as PipeWire sees it (see \"Find applications\")"),
    "split_placeholder": tr("Add an application by its PipeWire name (spotify, firefox…)…"),
    "capture_hint": tr("Direct capture records each screen without asking; the desktop portal asks "
                       "for permission on first launch (one screen)."),
    "manual_launch": tr("applications menu, run.sh"),
    "trash": tr("trash"),
    "reveal": tr("Show in folder"),
}
# Names under which voice chat applications show up in PipeWire; the first one is the default
# choice (Vesktop: Discord with audio sharing on Linux).
CHAT_APPS = ["vesktop", "discord", "WEBRTC VoiceEngine", "webcord", "legcord", "TeamSpeak", "Mumble",
             "Element", "Slack", "zoom"]
# On Wayland, a window cannot place itself on the game screen: confirmations then go through the
# desktop notifications.
OVERLAY_TOAST = os.environ.get("XDG_SESSION_TYPE", "").lower() != "wayland" and not os.environ.get("WAYLAND_DISPLAY")
DESKTOP_ID = "snappy"


# --------------------------------------------------------------------------
# Folders
# --------------------------------------------------------------------------
def _xdg(var: str, default: str) -> Path:
    value = os.environ.get(var)
    return Path(value) if value and os.path.isabs(value) else Path.home() / default


def data_dir(app: str) -> Path:
    """Configuration (~/.config/Snappy)."""
    return _xdg("XDG_CONFIG_HOME", ".config") / app


def cache_dir(app: str) -> Path:
    """Rebuildable caches (~/.cache/Snappy/cache)."""
    return _xdg("XDG_CACHE_HOME", ".cache") / app / "cache"


def buffer_dir(app: str) -> Path:
    """Replays just written, before they are assembled (the replay itself stays in memory). Not in
    $XDG_RUNTIME_DIR: the Flatpak gpu-screen-recorder has its own /run/user, where it would see
    neither the output folder nor the -sc script."""
    return _xdg("XDG_CACHE_HOME", ".cache") / app / "buffer"


def videos_dir() -> Path:
    """The user's "Videos" folder (xdg-user-dir), ~/Videos otherwise."""
    try:
        out = subprocess.run(["xdg-user-dir", "VIDEOS"], capture_output=True, text=True, timeout=3).stdout.strip()
        if out and Path(out) != Path.home():
            return Path(out)
    except (OSError, subprocess.SubprocessError):
        pass
    return Path.home() / "Videos"


# --------------------------------------------------------------------------
# Process setup
# --------------------------------------------------------------------------
def set_app_id() -> None:
    """The sound Snappy plays (editor, confirmation) is named "Snappy" in PipeWire: it can be put
    on its own track or left out of the game sound."""
    os.environ.setdefault("PULSE_PROP", f'application.name="{APP_NAME}" application.icon_name="{DESKTOP_ID}"')


def enable_dpi_awareness() -> None:
    pass


# --------------------------------------------------------------------------
# Starting Snappy (login, desktop shortcuts)
# --------------------------------------------------------------------------
def launch_args(extra: list[str] | None = None) -> list[str]:
    """Command that starts Snappy whatever the current folder: run.sh when Snappy runs from the
    environment install.sh creates, otherwise the current interpreter."""
    if getattr(sys, "frozen", False):
        return [sys.executable] + (extra or [])
    run_sh = SOURCE_ROOT / "run.sh"
    venv = SOURCE_ROOT / ".venv"
    if run_sh.exists() and Path(sys.prefix).resolve() == venv.resolve():
        return [str(run_sh)] + (extra or [])
    return ["env", f"PYTHONPATH={SOURCE_ROOT}", sys.executable, "-m", "snappy"] + (extra or [])


def short_command(args: list[str]) -> str:
    """Command to display: "snappy --hotkey save" if install.sh created the "snappy" command."""
    found = shutil.which(APP_NAME.lower())
    if found and args and args[0] == str(SOURCE_ROOT / "run.sh") and Path(found).resolve() == Path(args[0]).resolve():
        return shell_command([APP_NAME.lower()] + args[1:])
    return shell_command(args)


def desktop_exec(args: list[str]) -> str:
    """Exec= line of a .desktop file (quoting rules of the specification)."""
    def q(a: str) -> str:
        if a and not any(c in a for c in ' \t\n"\'\\><~|&;$*?#()`'):
            return a
        return '"' + "".join("\\" + c if c in '"`$\\' else c for c in a) + '"'
    return " ".join(q(a) for a in args).replace("%", "%%")


def shell_command(args: list[str]) -> str:
    return shlex.join(args)


def icon_path() -> Path:
    for p in (SOURCE_ROOT / "assets" / "snappy.png", Path(getattr(sys, "_MEIPASS", SOURCE_ROOT)) / "assets" / "snappy.png"):
        if p.exists():
            return p
    return SOURCE_ROOT / "assets" / "snappy.png"


def desktop_entry(args: list[str], name: str = APP_NAME, extra: str = "") -> str:
    return (f"[Desktop Entry]\nType=Application\nName={name}\nComment={tr('Instant replay of your screen')}\n"
            f"Exec={desktop_exec(args)}\nIcon={icon_path()}\nTerminal=false\nCategories=AudioVideo;Video;Recorder;\n"
            f"StartupWMClass={DESKTOP_ID}\n{extra}")


# --------------------------------------------------------------------------
# Foreground application (to name / group the clips)
# --------------------------------------------------------------------------
_IGNORED_APPS = {"snappy", "python", "python3", "plasmashell", "gnome-shell", "kwin_wayland", "kwin_x11",
                 "xfdesktop", "nautilus", "dolphin"}


def _run(args: list[str], timeout: float = 1.0) -> str:
    try:
        return subprocess.run(args, capture_output=True, text=True, timeout=timeout).stdout
    except (OSError, subprocess.SubprocessError):
        return ""


def _active_window() -> tuple[int, str, str]:
    """(pid, class, title) of the active window, depending on the desktop; (0, "", "") if unknown.
    GNOME on Wayland does not tell any application."""
    if os.environ.get("HYPRLAND_INSTANCE_SIGNATURE") and shutil.which("hyprctl"):
        try:
            w = json.loads(_run(["hyprctl", "activewindow", "-j"]) or "{}")
            return int(w.get("pid") or 0), w.get("class", ""), w.get("title", "")
        except (ValueError, TypeError):
            pass
    if os.environ.get("SWAYSOCK") and shutil.which("swaymsg"):
        try:
            stack = [json.loads(_run(["swaymsg", "-t", "get_tree"]) or "{}")]
            while stack:
                node = stack.pop()
                if node.get("focused") and node.get("pid"):
                    cls = node.get("app_id") or (node.get("window_properties") or {}).get("class", "")
                    return int(node["pid"]), cls or "", node.get("name") or ""
                stack += node.get("nodes", []) + node.get("floating_nodes", [])
        except (ValueError, TypeError):
            pass
    if shutil.which("kdotool") and "kde" in os.environ.get("XDG_CURRENT_DESKTOP", "").lower():
        wid = _run(["kdotool", "getactivewindow"]).strip()
        if wid:
            pid = _run(["kdotool", "getwindowpid", wid]).strip()
            cls = _run(["kdotool", "getwindowclassname", wid]).strip()
            name = _run(["kdotool", "getwindowname", wid]).strip()
            return int(pid) if pid.isdigit() else 0, cls, name
    if os.environ.get("DISPLAY") and not os.environ.get("WAYLAND_DISPLAY") and shutil.which("xprop"):
        out = _run(["xprop", "-root", "_NET_ACTIVE_WINDOW"])
        wid = out.rsplit(" ", 1)[-1].strip() if "#" in out else ""
        if wid and wid != "0x0":
            props = _run(["xprop", "-id", wid, "_NET_WM_PID", "WM_CLASS", "_NET_WM_NAME"])
            pid, cls, name = 0, "", ""
            for line in props.splitlines():
                key, _, value = line.partition("=")
                if key.startswith("_NET_WM_PID"):
                    pid = int(value.strip()) if value.strip().isdigit() else 0
                elif key.startswith("WM_CLASS"):
                    parts = [p.strip().strip('"') for p in value.split(",")]
                    cls = parts[-1] if parts else ""
                elif key.startswith("_NET_WM_NAME"):
                    name = value.strip().strip('"')
            return pid, cls, name
    return 0, "", ""


@functools.lru_cache(maxsize=64)
def _desktop_name(cls: str) -> str:
    """Readable name of an application from its .desktop file ("org.kde.dolphin" -> "Dolphin")."""
    if not cls:
        return ""
    low = cls.lower()
    dirs = [_xdg("XDG_DATA_HOME", ".local/share") / "applications"]
    dirs += [Path(d) / "applications" for d in os.environ.get("XDG_DATA_DIRS", "/usr/local/share:/usr/share").split(":") if d]
    dirs += [Path("/var/lib/flatpak/exports/share/applications"),
             Path.home() / ".local/share/flatpak/exports/share/applications"]
    for d in dirs:
        for f in glob.glob(str(d / "*.desktop")):
            stem = Path(f).stem.lower()
            try:
                text = Path(f).read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            wm_classes = [line[15:].strip().lower() for line in text.splitlines() if line.startswith("StartupWMClass=")]
            if stem == low or stem.endswith("." + low) or low in wm_classes:
                for line in text.splitlines():
                    if line.startswith("Name="):
                        return line[5:].strip()
    return ""


def foreground_app_info() -> tuple[str, str]:
    """(readable name, executable path) of the active application."""
    try:
        pid, cls, title = _active_window()
        if pid == os.getpid():
            return "", ""
        exe = ""
        if pid:
            try:
                exe = os.readlink(f"/proc/{pid}/exe")
            except OSError:
                exe = ""
        stem = Path(exe).name if exe else cls
        if stem.lower() in _IGNORED_APPS or (cls or "").lower() in _IGNORED_APPS:
            return "", ""
        name = _desktop_name(cls) or (cls[:1].upper() + cls[1:] if cls else "") or title or stem
        return name.strip()[:60], exe
    except Exception:
        return "", ""


# --------------------------------------------------------------------------
# File manager, trash
# --------------------------------------------------------------------------
def reveal(path: Path) -> None:
    """Opens the file manager with the file selected (D-Bus FileManager1)."""
    path = Path(path)
    try:
        from PySide6.QtDBus import QDBusConnection, QDBusMessage

        msg = QDBusMessage.createMethodCall("org.freedesktop.FileManager1", "/org/freedesktop/FileManager1",
                                            "org.freedesktop.FileManager1", "ShowItems")
        msg.setArguments([["file://" + quote(str(path.resolve()))], ""])
        reply = QDBusConnection.sessionBus().call(msg)
        if reply.type() != QDBusMessage.ErrorMessage:
            return
    except Exception:
        pass
    open_folder(path.parent)


def open_folder(path: Path) -> None:
    path = Path(path)
    path.mkdir(parents=True, exist_ok=True)
    subprocess.Popen(["xdg-open", str(path)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                     start_new_session=True)


def send_to_recycle_bin(paths: list[Path]) -> bool:
    """Desktop trash (freedesktop specification, recoverable from the file manager)."""
    from PySide6.QtCore import QFile

    ok = True
    for p in paths:
        result = QFile.moveToTrash(str(p))
        moved = result[0] if isinstance(result, tuple) else bool(result)
        if not moved and shutil.which("gio"):
            moved = subprocess.run(["gio", "trash", str(p)], capture_output=True).returncode == 0
        ok = ok and moved
    return ok


# --------------------------------------------------------------------------
# Start at login
# --------------------------------------------------------------------------
def _autostart_file() -> Path:
    return _xdg("XDG_CONFIG_HOME", ".config") / "autostart" / f"{DESKTOP_ID}.desktop"


def set_autostart(enabled: bool, minimized: bool = True) -> None:
    """Adds/removes Snappy from the login items (~/.config/autostart). `minimized`: started at
    login, it stays in the notification area; started by hand, it always opens its window."""
    path = _autostart_file()
    if not enabled:
        try:
            path.unlink()
        except FileNotFoundError:
            pass
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    args = launch_args(["--minimized"] if minimized else [])
    path.write_text(desktop_entry(args, extra="X-GNOME-Autostart-enabled=true\nX-KDE-autostart-after=panel\n"),
                    encoding="utf-8")


# --------------------------------------------------------------------------
# Appearance, Steam, sound
# --------------------------------------------------------------------------
# Named GNOME accent colors (GNOME 47 and later).
_GNOME_ACCENTS = {"blue": "#3584e4", "teal": "#2190a4", "green": "#3a944a", "yellow": "#c88800",
                  "orange": "#ed5b00", "red": "#e62d42", "pink": "#d56199", "purple": "#9141ac", "slate": "#6f8396"}


def accent_color() -> str:
    """Desktop accent color: KDE (kdeglobals), GNOME (accent-color), blue otherwise."""
    kdeglobals = _xdg("XDG_CONFIG_HOME", ".config") / "kdeglobals"
    if kdeglobals.exists():
        cfg = configparser.ConfigParser(interpolation=None, strict=False)
        try:
            cfg.read(kdeglobals, encoding="utf-8")
            for section, key in (("General", "AccentColor"), ("Colors:Selection", "BackgroundNormal")):
                value = cfg.get(section, key, fallback="")
                parts = [p.strip() for p in value.split(",")]
                if len(parts) >= 3 and all(p.isdigit() for p in parts[:3]):
                    r, g, b = (int(p) for p in parts[:3])
                    return f"#{r:02x}{g:02x}{b:02x}"
        except (configparser.Error, OSError, UnicodeDecodeError):
            pass
    if shutil.which("gsettings"):
        value = _run(["gsettings", "get", "org.gnome.desktop.interface", "accent-color"]).strip().strip("'")
        if value in _GNOME_ACCENTS:
            return _GNOME_ACCENTS[value]
    return "#3584e4"


STEAM_DIRS = ["~/.local/share/Steam", "~/.steam/steam", "~/.steam/root",
              "~/.var/app/com.valvesoftware.Steam/.local/share/Steam", "~/snap/steam/common/.local/share/Steam"]


def _running_appid() -> str | None:
    """Running Steam game: SteamAppId variable of the user's processes (the most frequent one)."""
    counts: dict[str, int] = {}
    uid = os.getuid()
    for pid in os.listdir("/proc"):
        if not pid.isdigit():
            continue
        try:
            if os.stat(f"/proc/{pid}").st_uid != uid:
                continue
            with open(f"/proc/{pid}/environ", "rb") as f:
                env = f.read()
        except OSError:
            continue
        for var in (b"SteamAppId=", b"SteamGameId="):
            i = env.find(var)
            if i >= 0:
                end = env.find(b"\0", i)
                val = env[i + len(var):end if end >= 0 else None].decode(errors="ignore")
                if val.isdigit() and val != "0" and len(val) < 10:
                    counts[val] = counts.get(val, 0) + 1
                break
    return max(counts, key=counts.get) if counts else None


def steam_value(name: str):
    """Equivalent of Steam's registry values on Windows (RunningAppID, SteamPath)."""
    if name == "RunningAppID":
        return _running_appid()
    if name == "SteamPath":
        return next((str(Path(d).expanduser()) for d in STEAM_DIRS if (Path(d).expanduser() / "steamapps").is_dir()), None)
    return None


def steam_dirs() -> list[str]:
    return [str(Path(d).expanduser().resolve()) for d in STEAM_DIRS]


def system_sounds() -> list[tuple[str, str, Path]]:
    """Desktop theme sounds (KDE Ocean, freedesktop) offered for the confirmation."""
    ocean = Path("/usr/share/sounds/ocean/stereo")
    fd = Path("/usr/share/sounds/freedesktop/stereo")
    candidates = [("ocean-success", tr("KDE — Success"), ocean / "completion-success.oga"),
                  ("ocean-outcome", tr("KDE — Achievement"), ocean / "outcome-success.oga"),
                  ("ocean-sent", tr("KDE — Sent"), ocean / "message-sent-instant.oga"),
                  ("fd-complete", tr("Desktop — Complete"), fd / "complete.oga"),
                  ("fd-message", tr("Desktop — Message"), fd / "message.oga"),
                  ("fd-camera", tr("Desktop — Camera"), fd / "camera-shutter.oga")]
    return [c for c in candidates if c[2].exists()]


_players: list[subprocess.Popen] = []


def play_wav(path: Path) -> None:
    """Plays a sound without waiting, under the name "Snappy" (can be isolated like the rest of Snappy)."""
    _players[:] = [p for p in _players if p.poll() is None]
    if shutil.which("pw-play"):
        cmd = ["pw-play", "-P", f'{{ application.name = "{APP_NAME}" media.role = "Notification" }}', str(path)]
    elif shutil.which("paplay"):
        cmd = ["paplay", f"--client-name={APP_NAME}", str(path)]
    elif shutil.which("aplay"):
        cmd = ["aplay", "-q", str(path)]
    else:
        return
    try:
        _players.append(subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL))
    except OSError:
        pass


@atexit.register
def _stop_players() -> None:
    for p in _players:
        if p.poll() is None:
            p.kill()
