"""Editor controller: opens a clip in the view, saves the edit, renames, changes the favorite or
the game, exports, and provides the timeline frames and the files to share."""

from __future__ import annotations

import time
from pathlib import Path

from PySide6.QtCore import QObject, QThreadPool, Signal

from ..i18n import tr
from ..models import edit as E
from ..models import games
from ..models.library import Clip, ClipLibrary
from ..models.settings import CODECS, Settings
from ..services import ffmpeg as ff
from ..services import media
from ..services.jobs import FramesJob
from ..views.editor import EditorPage
from ..views.widgets import fmt_size


class _ExportBridge(QObject):
    progress = Signal(float, str)
    done = Signal(bool, str)


class EditorController(QObject):
    clipChanged = Signal()          # rename, favorite, game, export: the gallery updates

    def __init__(self, view: EditorPage, library: ClipLibrary, settings: Settings):
        super().__init__(view)
        self.view, self.library, self.settings = view, library, settings
        self.clip: Clip | None = None
        self.exporter: media.Exporter | None = None
        self._jobs: list[media.ExportSpec] = []
        self._outputs: list[str] = []
        self._export_sig = ""
        self._export_started = 0.0
        self._bridge = _ExportBridge()
        self._bridge.progress.connect(view.export_progress)
        self._bridge.done.connect(self._on_export_done)

        view.saveEditRequested.connect(self.save_edit)
        view.renameRequested.connect(self.rename)
        view.favoriteToggled.connect(self.toggle_favorite)
        view.gamePickRequested.connect(self.pick_game)
        view.exportRequested.connect(self.export)
        view.exportCancelRequested.connect(self.cancel_export)
        view.normalizeChanged.connect(self.set_normalize)

    def apply_settings(self, settings: Settings) -> None:
        self.settings = settings

    # ------------------------------------------------------------ opening
    def open(self, clip: Clip) -> None:
        self.clip = clip
        self.library.ensure_metadata(clip)
        path = self.library.path(clip)
        self.view.show_clip(clip, path, self._codecs())
        self._push_share()
        job = FramesJob(path, clip.duration or 30, max(1, clip.n_screens))
        job.signals.done.connect(self.view.set_frames)
        QThreadPool.globalInstance().start(job)

    def close(self) -> None:
        self.view.close_clip()
        self.clip = None

    def _codecs(self) -> dict[str, tuple[bool, str]]:
        """{codec: (can be encoded on this machine, encoder used)} for the export settings."""
        out = {}
        for codec in CODECS:
            ok = codec == "h264" or ff.codec_of(media.ENCODERS.get(codec, "gpu")) == codec or \
                ff.encoder_works(ff.CPU_ENCODERS[codec])
            enc = media.ENCODERS.get(codec, self.settings.export_encoder) if media.ENCODERS.ready else ""
            out[codec] = (ok, ff.VENDOR_LABELS[ff.vendor_of(enc)] if enc else "")
        return out

    def _push_share(self) -> None:
        c = self.clip
        if not c:
            return
        path = self.library.path(c)
        self.view.show_share({
            "thumb": self.library.thumb_path(c), "clip": path,
            "latest": self.library.latest_export(c), "latest_mp3": self.library.latest_export(c, ".mp3"),
            "audio_resolve": lambda p=path: media.audio_mp3(p, 0),
        })

    # ------------------------------------------------------------ edit and clip
    def save_edit(self, edit: dict, angles: list) -> None:
        clip = self.clip
        if clip and self.library.get(clip.file):
            self.library.update(clip, edit=edit, angles=angles)

    def set_normalize(self, on: bool) -> None:
        self.settings.normalize_audio = on
        try:
            self.settings.save()
        except OSError:
            pass

    def toggle_favorite(self) -> None:
        if self.clip:
            self.library.update(self.clip, favorite=not self.clip.favorite)
            self.view.refresh_clip()
            self.clipChanged.emit()

    def pick_game(self) -> None:
        if not self.clip:
            return
        known = {name: appid for name, _n, appid, _exe in self.library.groups() if appid}
        choice = self.view.ask_game(known, self.clip.game)
        if choice is not None:
            name, appid = choice
            self.library.update(self.clip, game=name, appid=(appid or games.appid_for(name) or "") if name else "")
            self.view.refresh_clip()
            self.clipChanged.emit()

    def rename(self, text: str) -> None:
        clip = self.clip
        if not clip or text == clip.title:
            return
        pos, playing = self.view.release_media()
        try:
            self.library.rename(clip, text)
        except FileExistsError:
            self.view.warn(tr("Rename"), tr("A clip already has this name."))
        except OSError as exc:
            self.view.warn(tr("Rename"), tr("Could not rename: {error}", error=exc))
        self.view.reload_media(self.library.path(clip), pos, playing)
        self._push_share()
        self.clipChanged.emit()

    # ------------------------------------------------------------ export
    def export(self, req: dict) -> None:
        """Video (with the sound), then the sound alone as MP3 (same edit, same volume)."""
        c = self.clip
        if not c or self.exporter:
            return
        s = req["settings"]
        out_dir = self.library.exports_dir
        out = out_dir / f"{c.stem}-{E.export_tag(s, c.height, c.fps, c.n_screens)}.mp4"
        segments, tracks = req["segments"], req["tracks"]
        plan = E.video_plan(segments, req["angles"], s["screen"]) if c.n_screens > 1 and not s["copy"] else None
        spec = media.ExportSpec(src=str(self.library.path(c)), out=str(out), segments=segments, tracks=tracks,
                                height=s["height"], fps=s["fps"], codec=s["codec"], target_mb=s["target"],
                                qp=s["qp"], copy=s["copy"], normalize=req["normalize"],
                                plan=plan, n_screens=c.n_screens, src_size=(c.width or 1920, c.height or 1080),
                                src_fps=c.fps or 60)
        self._jobs = [spec]
        if any(not t["muted"] for t in tracks):
            self._jobs.append(media.ExportSpec(**{**spec.__dict__, "out": str(out_dir / f"{c.stem}-audio.mp3"),
                                                  "audio_only": True, "copy": False, "plan": None}))
        self._export_sig = req["signature"]
        self._export_started = time.monotonic()
        self._outputs = []
        self.view.export_started()
        self._run_next()

    def cancel_export(self) -> None:
        if self.exporter:
            self.exporter.cancel()

    def _run_next(self) -> None:
        spec = self._jobs.pop(0)
        prev = self.exporter
        self.exporter = media.Exporter(spec, self.settings.export_encoder,
                                       on_progress=lambda f, step: self._bridge.progress.emit(f, step),
                                       on_done=lambda ok, err: self._bridge.done.emit(ok, err))
        if prev and spec.audio_only:   # same mix, same normalization gain (no second analysis)
            self.exporter.norm_gain, self.exporter.limit = prev.norm_gain, prev.limit
        self._outputs.append(spec.out)
        self.exporter.start()

    def _on_export_done(self, ok: bool, err: str) -> None:
        if ok and self._jobs:
            self._run_next()
            return
        self.exporter = None
        self._jobs = []
        message = ""
        if ok and self.clip:
            self.library.update(self.clip, export_sig=self._export_sig)
            sizes = " + ".join(fmt_size(Path(p).stat().st_size) for p in self._outputs if Path(p).exists())
            message = tr("Exported in {seconds} s · {sizes} (Exports folder)",
                         seconds=f"{time.monotonic() - self._export_started:.1f}", sizes=sizes)
            self.clipChanged.emit()
        elif err:
            message = tr("Export failed: {error}", error=err[:160])
        self.view.export_finished(message)
        self._push_share()
