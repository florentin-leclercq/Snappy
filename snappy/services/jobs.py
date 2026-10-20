"""Background tasks (QThreadPool): saving a replay, gallery thumbnails and previews, editor
timeline frames. They report their result to the UI thread."""

from __future__ import annotations

import logging
from pathlib import Path

from PySide6.QtCore import QObject, QRunnable, Signal

from ..models.library import ClipLibrary
from ..models.settings import cache_dir
from . import ffmpeg as ff
from .recorder import ReplayBuffer

log = logging.getLogger(__name__)


class JobSignals(QObject):
    done = Signal(object, str)


class FramesSignals(QObject):
    done = Signal(str, list)


class SaveClipJob(QRunnable):
    """Saving a replay: takes the last seconds out of the buffer and adds them to the library.
    `done(clip, error)`."""

    def __init__(self, recorder: ReplayBuffer, library: ClipLibrary, context: dict):
        super().__init__()
        self.recorder, self.library, self.context = recorder, library, context
        self.signals = JobSignals()

    def run(self):
        try:
            ctx = self.context
            saved = self.recorder.save_clip(self.library.folder, name=ctx.get("name"))
            clip = self.library.add(saved.path, saved.created, saved.duration, app=ctx.get("app", ""),
                                    title=saved.path.stem, app_exe=ctx.get("exe", ""), game=ctx.get("game") or "",
                                    appid=ctx.get("appid") or "", tracks=saved.tracks, screens=saved.screens,
                                    mix_track=saved.mix_track)
            self.signals.done.emit(clip, "")
        except Exception as exc:
            log.exception("Save failed")
            self.signals.done.emit(None, str(exc))


class StopRecordingJob(SaveClipJob):
    """End of a manual recording: writes the whole recording as a clip. `done(clip, error)`."""

    def run(self):
        try:
            ctx = self.context
            saved = self.recorder.stop_recording(self.library.folder, name=ctx.get("name"))
            clip = self.library.add(saved.path, saved.created, saved.duration, app=ctx.get("app", ""),
                                    title=saved.path.stem, app_exe=ctx.get("exe", ""), game=ctx.get("game") or "",
                                    appid=ctx.get("appid") or "", tracks=saved.tracks, screens=saved.screens,
                                    mix_track=saved.mix_track)
            self.signals.done.emit(clip, "")
        except Exception as exc:
            log.exception("Recording failed")
            self.signals.done.emit(None, str(exc))


class ThumbJob(QRunnable):
    """Gallery thumbnails; with `sprite`, the hover bar then the preview video.
    `done(clip, path)` for each file produced."""

    def __init__(self, library: ClipLibrary, clips: list, sprite: bool = False):
        super().__init__()
        self.library, self.clips, self.sprite = library, clips, sprite
        self.signals = JobSignals()

    def run(self):
        for clip in self.clips:
            if self.sprite:   # hovering a card: position bar, then preview video
                path = self.library.ensure_sprite(clip)
                self.signals.done.emit(clip, str(path) if path else "")
                path = self.library.ensure_preview(clip)
            else:
                self.library.ensure_metadata(clip)
                path = self.library.ensure_thumbnail(clip)
            self.signals.done.emit(clip, str(path) if path else "")


class FramesJob(QRunnable):
    """Frames spread over the clip, for each screen: filmstrip and screen rows of the timeline.

    Only keyframes are decoded (one per second, `-skip_frame nokey`): a few dozen frames instead
    of the whole clip, which would compete with playback and the buffer.
    The result is kept in a disk cache: opening the clip again is instant.
    `done(path, [[JPEG bytes of a screen], ...])`."""

    MAX_FRAMES = 40

    def __init__(self, path: Path, duration: float, screens: int = 1):
        super().__init__()
        self.path, self.duration, self.screens = Path(path), duration, max(1, screens)
        self.signals = FramesSignals()

    def _cache(self) -> Path:
        st = self.path.stat()
        return cache_dir() / "frames" / f"{self.path.stem}-{st.st_size}-{int(st.st_mtime)}"

    def run(self):
        out: list[list[bytes]] = []
        try:
            folder = self._cache()
            if not (folder / "done").is_file():
                folder.mkdir(parents=True, exist_ok=True)
                step = max(1, -(-int(self.duration) // self.MAX_FRAMES))   # 1 keyframe out of `step`
                args = [ff.ffmpeg(), "-hide_banner", "-loglevel", "error", "-y", "-skip_frame", "nokey",
                        "-i", str(self.path)]
                for k in range(self.screens):
                    args += ["-map", f"0:v:{k}", "-fps_mode", "vfr",
                             "-vf", f"select='not(mod(n\\,{step}))',scale=160:-2", "-q:v", "6",
                             str(folder / f"s{k}_%03d.jpg")]
                ff.run(args, timeout=90, background=True)
                (folder / "done").write_text("ok", encoding="utf-8")
            # Bytes: the QPixmaps must be created in the UI thread.
            out = [[p.read_bytes() for p in sorted(folder.glob(f"s{k}_*.jpg"))] for k in range(self.screens)]
        except Exception:
            out = []
        self.signals.done.emit(str(self.path), out)
