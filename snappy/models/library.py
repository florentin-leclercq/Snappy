"""Clip library: JSON index, thumbnails, favorites, games, trash, storage quota."""

from __future__ import annotations

import json
import logging
import shutil
import threading
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta
from pathlib import Path

from ..i18n import tr
from ..services import ffmpeg as ff

log = logging.getLogger(__name__)

INDEX_NAME = ".snappy-index.json"
THUMBS_DIR = ".thumbs"
TRASH_DIR = ".trash"
LEGACY_TRASH_DIRS = (".corbeille",)   # name used by older versions: emptied too
EXPORTS_DIR = "Exports"
VIDEO_EXTS = {".mp4", ".mkv", ".mov"}
SPRITE_FRAMES = 40          # frames of a card's hover bar (to seek in the clip)
SPRITE_W = 192
PREVIEW_W = 480             # preview video played when hovering a card (small, no sound)
# Track names found in the MP4 of a Snappy clip (both languages) -> kind of track.
TRACK_KINDS = {"Game": "game", "Jeu": "game", "Other sounds": "game", "Autres sons": "game",
               "Voice chat": "chat", "Chat vocal": "chat", "Microphone": "mic", "Micro": "mic"}


def safe_filename(name: str) -> str:
    return "".join("-" if c in '/\\:*?"<>|' else c for c in name).strip(" .") or "Clip"


@dataclass
class Clip:
    file: str                       # file name in the clips folder
    title: str
    created: str                    # ISO 8601
    duration: float = 0.0
    size: int = 0
    app: str = ""                   # foreground application at capture time
    app_exe: str = ""               # its executable (for the icon)
    game: str = ""                  # Steam game (detected or chosen)
    appid: str = ""
    favorite: bool = False
    tags: list[str] = field(default_factory=list)
    width: int = 0
    height: int = 0
    fps: int = 0
    tracks: list = field(default_factory=list)    # separate tracks: [{kind, label, source}]
    mix_track: bool = False                       # 1st audio track of the file = mix of the others
    screens: list = field(default_factory=list)   # [{monitor, label}]: video streams of the file
    angles: list = field(default_factory=list)    # multi-screen edit: [[time, screen number]]
    edit: dict = field(default_factory=dict)      # last editor state
    export_sig: str = ""                          # signature of the edit of the last export

    @property
    def created_dt(self) -> datetime:
        try:
            return datetime.fromisoformat(self.created)
        except ValueError:
            return datetime.fromtimestamp(0)

    @property
    def stem(self) -> str:
        return Path(self.file).stem

    @property
    def group(self) -> str:
        """Sidebar grouping: the game, otherwise the application."""
        return self.game or self.app

    @property
    def n_screens(self) -> int:
        return max(1, len(self.screens))

    def separate_tracks(self) -> list[tuple[int, dict]]:
        """(audio stream index in the file, description) of the separate tracks."""
        offset = 1 if self.mix_track else 0
        return [(i + offset, t) for i, t in enumerate(self.tracks)]


class ClipLibrary:
    def __init__(self, folder: str | Path):
        self.folder = Path(folder)
        self._lock = threading.RLock()
        self._clips: dict[str, Clip] = {}
        self._trashed: dict[str, tuple[float, list[Clip]]] = {}
        self.folder.mkdir(parents=True, exist_ok=True)
        self.reload()

    # ------------------------------------------------------------ index
    @property
    def index_path(self) -> Path:
        return self.folder / INDEX_NAME

    @property
    def exports_dir(self) -> Path:
        return self.folder / EXPORTS_DIR

    def thumb_path(self, clip: Clip) -> Path:
        return self.folder / THUMBS_DIR / (clip.stem + ".jpg")

    def sprite_path(self, clip: Clip) -> Path:
        return self.folder / THUMBS_DIR / (clip.stem + ".scrub.jpg")

    def preview_path(self, clip: Clip) -> Path:
        return self.folder / THUMBS_DIR / (clip.stem + ".preview.mp4")

    def _side_files(self, clip: Clip) -> list[Path]:
        """Files derived from a clip (thumbnail, hover bar, preview video)."""
        return [self.thumb_path(clip), self.sprite_path(clip), self.preview_path(clip)]

    def path(self, clip: Clip) -> Path:
        return self.folder / clip.file

    def reload(self) -> None:
        with self._lock:
            try:
                raw = json.loads(self.index_path.read_text(encoding="utf-8"))
                known = set(Clip.__dataclass_fields__)
                self._clips = {c["file"]: Clip(**{k: v for k, v in c.items() if k in known})
                               for c in raw.get("clips", [])}
            except (OSError, ValueError, TypeError):
                self._clips = {}
            # Sync with the disk: files deleted / added by hand.
            on_disk = {p.name: p for p in self.folder.iterdir()
                       if p.is_file() and p.suffix.lower() in VIDEO_EXTS
                       and not p.name.startswith(".") and ".part" not in p.name}
            for name in list(self._clips):
                if name not in on_disk:
                    del self._clips[name]
            for name, p in on_disk.items():
                if name not in self._clips:
                    st = p.stat()
                    self._clips[name] = Clip(
                        file=name, title=p.stem,
                        created=datetime.fromtimestamp(st.st_mtime).isoformat(timespec="seconds"),
                        size=st.st_size)
            self._save()
        self.purge_trash(older_than=0)   # trash from a previous session

    def _save(self) -> None:
        data = {"version": 2, "clips": [asdict(c) for c in self._clips.values()]}
        tmp = self.index_path.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, indent=1, ensure_ascii=False), encoding="utf-8")
        tmp.replace(self.index_path)

    # ------------------------------------------------------------ queries
    def clips(self, favorites_only: bool = False, group: str | None = None, search: str = "",
              period: str = "all", day: tuple[int, int, int] | None = None, sort: str = "recent") -> list[Clip]:
        """period: all | today | 7 | 30; sort: recent | old | long | heavy."""
        with self._lock:
            items = list(self._clips.values())
        if favorites_only:
            items = [c for c in items if c.favorite]
        if group == "":
            items = [c for c in items if not c.group]
        elif group:
            items = [c for c in items if c.group == group]
        if search:
            q = search.lower()
            items = [c for c in items if q in c.title.lower() or q in c.group.lower() or q in c.file.lower()]
        if day:
            items = [c for c in items if (c.created_dt.year, c.created_dt.month, c.created_dt.day) == day]
        elif period != "all":
            midnight = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
            since = midnight if period == "today" else midnight - timedelta(days=int(period) - 1)
            items = [c for c in items if c.created_dt >= since]
        key = {"recent": (lambda c: c.created, True), "old": (lambda c: c.created, False),
               "long": (lambda c: c.duration, True), "heavy": (lambda c: c.size, True)}[sort]
        return sorted(items, key=key[0], reverse=key[1])

    def groups(self) -> list[tuple[str, int, str, str]]:
        """Games and applications: [(name, clip count, appid, executable)], largest first."""
        out: dict[str, list] = {}
        with self._lock:
            for c in self._clips.values():
                if not c.group:
                    continue
                g = out.setdefault(c.group, [0, "", ""])
                g[0] += 1
                g[1] = g[1] or c.appid
                g[2] = g[2] or c.app_exe
        return sorted(((k, v[0], v[1], v[2]) for k, v in out.items()), key=lambda t: (-t[1], t[0].lower()))

    def apps(self) -> list[str]:
        return [g[0] for g in self.groups()]

    def count_ungrouped(self) -> int:
        with self._lock:
            return sum(1 for c in self._clips.values() if not c.group)

    def total_size(self) -> int:
        with self._lock:
            return sum(c.size for c in self._clips.values())

    def get(self, file: str) -> Clip | None:
        with self._lock:
            return self._clips.get(file)

    # ------------------------------------------------------------ mutations
    def add(self, path: Path, created: datetime, duration: float, app: str = "",
            title: str | None = None, **extra) -> Clip:
        path = Path(path)
        clip = Clip(file=path.name,
                    title=title or path.stem,
                    created=created.isoformat(timespec="seconds"),
                    duration=round(duration, 3), size=path.stat().st_size, app=app,
                    **{k: v for k, v in extra.items() if k in Clip.__dataclass_fields__})
        self._fill_media_info(clip)
        with self._lock:
            self._clips[clip.file] = clip
            self._save()
        self.ensure_thumbnail(clip)
        return clip

    def _fill_media_info(self, clip: Clip) -> None:
        try:
            info = ff.media_info(self.path(clip))
        except Exception:
            return
        clip.width, clip.height, clip.fps = info["width"], info["height"], info["fps"]
        if not clip.duration:
            clip.duration = round(info["duration"], 3)
        if not clip.screens and info["video_streams"] > 1:
            clip.screens = [{"monitor": i, "label": info["video_names"][i] or tr("Screen {n}", n=i + 1)}
                            for i in range(info["video_streams"])]
        names = info["audio_names"]
        if not clip.tracks and len(names) > 1 and names[0] == "Mix" and all(names[1:]):
            # Snappy file indexed again: the Mix track and the track names are in the MP4.
            clip.mix_track = True
            clip.tracks = [{"kind": TRACK_KINDS.get(n, "app"), "label": n, "source": ""} for n in names[1:]]
        if not clip.tracks and info["audio_streams"]:
            n = info["audio_streams"]
            guess = {1: ["game"], 2: ["game", "mic"], 3: ["game", "chat", "mic"]}.get(n)
            from .settings import SOURCE_LABELS

            clip.tracks = ([{"kind": k, "label": SOURCE_LABELS[k], "source": ""} for k in guess] if guess else
                           [{"kind": "other", "label": tr("Track {n}", n=i + 1), "source": ""} for i in range(n)])
            if n == 1:
                clip.tracks[0]["label"] = tr("Sound")

    def ensure_thumbnail(self, clip: Clip) -> Path | None:
        thumb = self.thumb_path(clip)
        if thumb.is_file():
            return thumb
        at = min(1.0, max(0.0, clip.duration / 2)) if clip.duration else 1.0
        if ff.make_thumbnail(self.path(clip), thumb, at=at):
            return thumb
        return None

    def ensure_preview(self, clip: Clip) -> Path | None:
        """Small silent video (480 px, 30 fps) looped when hovering a card: playing it costs much less
        than the original clip (several screens in 1440p)."""
        out = self.preview_path(clip)
        if out.is_file():
            return out
        tmp = out.with_suffix(".part.mp4")
        out.parent.mkdir(parents=True, exist_ok=True)
        try:
            ff.run([ff.ffmpeg(), "-hide_banner", "-loglevel", "error", "-y", "-i", str(self.path(clip)),
                    "-map", "0:v:0", "-an", "-vf", f"fps=30,scale={PREVIEW_W}:-2", "-c:v", "libx264",
                    "-preset", "veryfast", "-crf", "30", "-pix_fmt", "yuv420p", "-movflags", "+faststart",
                    "-f", "mp4", str(tmp)], timeout=300, background=True)
            tmp.replace(out)
        except Exception:
            tmp.unlink(missing_ok=True)
            return None
        return out

    def ensure_sprite(self, clip: Clip) -> Path | None:
        """Strip of SPRITE_FRAMES frames spread over the clip (preview when hovering the cards)."""
        sprite = self.sprite_path(clip)
        if sprite.is_file():
            return sprite
        if clip.duration <= 0:
            return None
        sprite.parent.mkdir(parents=True, exist_ok=True)
        fps = SPRITE_FRAMES / max(clip.duration, 0.1)
        try:
            ff.run([ff.ffmpeg(), "-hide_banner", "-loglevel", "error", "-y", "-skip_frame", "nokey", "-i",
                    str(self.path(clip)), "-map", "0:v:0", "-vf",
                    f"fps={fps:.5f},scale={SPRITE_W}:-2,tile={SPRITE_FRAMES}x1",
                    "-frames:v", "1", "-q:v", "5", str(sprite)], timeout=60, background=True)
        except Exception:
            return None
        return sprite if sprite.is_file() else None

    def ensure_metadata(self, clip: Clip) -> None:
        """Fills in the length and info of clips added to the folder by hand."""
        if clip.duration > 0 and clip.height:
            return
        try:
            if not clip.duration:
                clip.duration = round(ff.probe_duration(self.path(clip)), 3)
            self._fill_media_info(clip)
            with self._lock:
                self._save()
        except Exception:
            pass

    def update(self, clip: Clip, **changes) -> None:
        with self._lock:
            for k, v in changes.items():
                setattr(clip, k, v)
            self._clips[clip.file] = clip
            self._save()

    def rename(self, clip: Clip, title: str) -> Clip:
        """Renames the clip AND its file (the file name is what people see when it is shared).
        Raises FileExistsError if the name is taken."""
        title = title.strip()
        stem = safe_filename(title)
        new_file = stem + Path(clip.file).suffix
        with self._lock:
            if new_file == clip.file:
                self.update(clip, title=title)
                return clip
            if (self.folder / new_file).exists() or new_file in self._clips:
                raise FileExistsError(new_file)
            old_stem = clip.stem
            self.path(clip).rename(self.folder / new_file)
            for old in self._side_files(clip):
                try:
                    old.rename(old.with_name(stem + old.name[len(old_stem):]))
                except OSError:
                    pass
            if self.exports_dir.is_dir():
                for e in self.exports_dir.iterdir():
                    if e.name.startswith(old_stem + "-"):
                        try:
                            e.rename(e.with_name(stem + e.name[len(old_stem):]))
                        except OSError:
                            pass
            del self._clips[clip.file]
            clip.file, clip.title = new_file, title
            self._clips[new_file] = clip
            self._save()
        return clip

    def delete(self, clip: Clip) -> None:
        """Permanent deletion (quota)."""
        with self._lock:
            for p in [self.path(clip)] + self._side_files(clip):
                try:
                    p.unlink()
                except OSError:
                    pass
            self._clips.pop(clip.file, None)
            self._save()

    # ------------------------------------------------------------ trash (undoable)
    def trash(self, clips: list[Clip]) -> str:
        """Sets clips aside (.trash folder): `undo_trash` puts them back, otherwise they go to the
        system trash (recoverable) after a few seconds."""
        token = f"{time.time():.6f}"
        moved = []
        bin_dir = self.folder / TRASH_DIR / token
        bin_dir.mkdir(parents=True, exist_ok=True)
        with self._lock:
            for clip in clips:
                try:
                    self.path(clip).rename(bin_dir / clip.file)
                except OSError:
                    log.warning("Could not move %s", clip.file)
                    continue
                for p in self._side_files(clip):
                    try:
                        p.rename(bin_dir / p.name)
                    except OSError:
                        pass
                self._clips.pop(clip.file, None)
                moved.append(clip)
            self._trashed[token] = (time.time(), moved)
            self._save()
        return token

    def undo_trash(self, token: str) -> int:
        with self._lock:
            entry = self._trashed.pop(token, None)
            if not entry:
                return 0
            bin_dir = self.folder / TRASH_DIR / token
            restored = 0
            for clip in entry[1]:
                try:
                    (bin_dir / clip.file).rename(self.path(clip))
                except OSError:
                    continue
                for p in self._side_files(clip):
                    try:
                        (bin_dir / p.name).rename(p)
                    except OSError:
                        pass
                self._clips[clip.file] = clip
                restored += 1
            shutil.rmtree(bin_dir, ignore_errors=True)
            self._save()
        return restored

    def purge_trash(self, older_than: float = 8.0) -> None:
        """Sends what has been set aside for more than `older_than` s to the system trash."""
        from ..platforms import system

        now = time.time()
        for name in (TRASH_DIR,) + LEGACY_TRASH_DIRS:
            root = self.folder / name
            if not root.is_dir():
                continue
            with self._lock:
                for d in list(root.iterdir()):
                    entry = self._trashed.get(d.name)
                    if entry and now - entry[0] < older_than:
                        continue
                    self._trashed.pop(d.name, None)
                    files = [p for p in d.iterdir() if p.suffix.lower() in VIDEO_EXTS]
                    if files and not system.send_to_recycle_bin(files):
                        continue   # retried later
                    shutil.rmtree(d, ignore_errors=True)
                try:
                    root.rmdir()
                except OSError:
                    pass

    def enforce_quota(self, max_gb: float) -> list[Clip]:
        """Deletes the oldest non-favorite clips beyond the quota."""
        if max_gb <= 0:
            return []
        limit = int(max_gb * 1024 ** 3)
        removed: list[Clip] = []
        with self._lock:
            total = self.total_size()
            for clip in sorted(self._clips.values(), key=lambda c: c.created):
                if total <= limit:
                    break
                if clip.favorite:
                    continue
                total -= clip.size
                self.delete(clip)
                removed.append(clip)
        return removed

    def move_to(self, new_folder: str | Path) -> None:
        """Switches folder (existing clips stay where they are)."""
        self.folder = Path(new_folder)
        self.folder.mkdir(parents=True, exist_ok=True)
        self.reload()

    def free_space_bytes(self) -> int:
        try:
            return shutil.disk_usage(self.folder).free
        except OSError:
            return 0

    # ------------------------------------------------------------ exports
    def latest_export(self, clip: Clip, ext: str = ".mp4") -> Path | None:
        """Latest export of this clip (Exports folder)."""
        if not self.exports_dir.is_dir():
            return None
        prefix = clip.stem + "-"
        files = [p for p in self.exports_dir.iterdir()
                 if p.is_file() and p.name.startswith(prefix) and p.suffix.lower() == ext and ".part" not in p.name]
        return max(files, key=lambda p: p.stat().st_mtime) if files else None
