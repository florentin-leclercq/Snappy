"""Steam games: running game, installed games and artwork already cached (nothing is downloaded)."""

from __future__ import annotations

import functools
import logging
import os
from pathlib import Path

log = logging.getLogger(__name__)

_SKIP = ("proton", "steamworks", "steam linux runtime", "redistributable", "soundtrack", "steamvr")


def _reg_value(name: str):
    """Value Steam keeps up to date (install path, running game)."""
    from ..platforms import system

    return system.steam_value(name)


@functools.lru_cache(maxsize=1)
def steam_root() -> Path | None:
    from ..platforms import system

    for c in system.steam_dirs():
        if c and (Path(c) / "steamapps").is_dir():
            return Path(c)
    return None


@functools.lru_cache(maxsize=1)
def _libraries() -> tuple[Path, ...]:
    root = steam_root()
    if not root:
        return ()
    libs = [root / "steamapps"]
    try:
        for line in (root / "steamapps" / "libraryfolders.vdf").read_text(encoding="utf-8", errors="replace").splitlines():
            parts = line.strip().split('"')
            if len(parts) >= 4 and parts[1] == "path":
                libs.append(Path(parts[3].replace("\\\\", "\\")) / "steamapps")
    except OSError:
        pass
    seen, out = set(), []
    for lib in libs:
        key = str(lib).lower()
        if key not in seen and lib.is_dir():
            seen.add(key)
            out.append(lib)
    return tuple(out)


def _manifest_name(path: Path) -> str | None:
    try:
        for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
            parts = line.strip().split('"')
            if len(parts) >= 4 and parts[1] == "name":
                return parts[3]
    except OSError:
        pass
    return None


def game_name(appid: str) -> str | None:
    for lib in _libraries():
        name = _manifest_name(lib / f"appmanifest_{appid}.acf")
        if name:
            return name
    return None


def running_game() -> tuple[str | None, str | None]:
    """Running Steam game (Steam RunningAppID key): (name, appid) or (None, None)."""
    appid = _reg_value("RunningAppID")
    if not appid:
        return None, None
    appid = str(appid)
    name = game_name(appid)
    if not name or any(k in name.lower() for k in _SKIP):
        return None, None
    return name, appid


@functools.lru_cache(maxsize=1)
def installed_games() -> tuple[tuple[str, str], ...]:
    """[(name, appid)] of the installed Steam games, without tools or redistributables."""
    out = {}
    for lib in _libraries():
        for f in lib.glob("appmanifest_*.acf"):
            name = _manifest_name(f)
            if name and not any(k in name.lower() for k in _SKIP):
                out[name] = f.stem[len("appmanifest_"):]
    return tuple(sorted(out.items(), key=lambda kv: kv[0].lower()))


def appid_for(name: str) -> str | None:
    low = (name or "").lower()
    return next((a for n, a in installed_games() if n.lower() == low), None)


@functools.lru_cache(maxsize=256)
def game_art(appid: str | None) -> dict[str, str]:
    """Game images already in Steam's cache: icon, logo, cover, header, hero."""
    root = steam_root()
    if not appid or not root:
        return {}
    cache = root / "appcache" / "librarycache"
    art: dict[str, str] = {}
    wanted = {"logo.png": "logo", "library_600x900.jpg": "cover", "library_header.jpg": "header",
              "header.jpg": "header", "library_hero.jpg": "hero"}
    d = cache / str(appid)
    if d.is_dir():   # recent layout: one folder per game
        for dirpath, _dirs, files in os.walk(d):
            for f in files:
                full = os.path.join(dirpath, f)
                if f in wanted:
                    art.setdefault(wanted[f], full)
                elif Path(dirpath) == d and f.endswith(".jpg") and len(f) == 44:
                    art.setdefault("icon", full)   # <sha1>.jpg at the root = icon
    for suffix, key in (("_icon.jpg", "icon"), ("_logo.png", "logo"), ("_library_600x900.jpg", "cover"),
                        ("_header.jpg", "header"), ("_library_hero.jpg", "hero")):
        f = cache / f"{appid}{suffix}"   # old flat layout
        if f.exists():
            art.setdefault(key, str(f))
    return art
