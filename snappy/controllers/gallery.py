"""Gallery controller: actions on the clips (favorite, game, rename, trash, quick export for
Discord, sound as MP3), thumbnails and hover previews, delayed trash."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QObject, QThreadPool, QTimer, Signal
from PySide6.QtGui import QPixmap

from ..i18n import tr
from ..models import edit as E
from ..models import games
from ..models.library import Clip, ClipLibrary
from ..models.settings import Settings
from ..services import media
from ..services.jobs import ThumbJob
from ..views.main_window import MainWindow
from ..views.widgets import copy_files, fmt_size


class _QuickBridge(QObject):
    done = Signal(object, bool, str, str)


class GalleryController(QObject):
    def __init__(self, window: MainWindow, library: ClipLibrary, settings: Settings):
        super().__init__(window)
        self.window, self.library, self.settings = window, library, settings
        # Queue for thumbnails / previews only: two FFmpeg at a time at most, low priority.
        self.media_pool = QThreadPool(self)
        self.media_pool.setMaxThreadCount(2)
        self._thumbs_pending: set[str] = set()
        self._sprites_pending: set[str] = set()
        self._quick = _QuickBridge()
        self._quick.done.connect(self._on_quick_done)
        self._quick_queue: list = []
        self._quick_busy = False
        self._quick_total = self._quick_ok = 0
        self._size_timer = QTimer(self, singleShot=True, interval=500, timeout=self._save_settings)

        w = window
        w.thumbsNeeded.connect(self.load_thumbs)
        w.spriteNeeded.connect(self.load_sprite)
        w.thumbSizeChanged.connect(self._on_thumb_size)
        w.favoriteToggled.connect(self.toggle_favorite)
        w.favoritesSet.connect(self.set_favorites)
        w.gameChosen.connect(self.set_game)
        w.renameChosen.connect(self.rename)
        w.trashRequested.connect(self.trash)
        w.quickExportRequested.connect(self.quick_export)
        w.copyAudioRequested.connect(self.copy_audio)
        # Delayed trash: sent to the system trash after a few seconds.
        QTimer(self, interval=4_000, timeout=lambda: self.library.purge_trash(10.0)).start()

    def apply_settings(self, settings: Settings) -> None:
        self.settings = settings

    def _save_settings(self) -> None:
        try:
            self.settings.save()
        except OSError:
            pass

    def _on_thumb_size(self, w: int) -> None:
        self.settings.thumb_size = w
        self._size_timer.start()

    # ------------------------------------------------------------ favorites, game, name
    def toggle_favorite(self, clip: Clip) -> None:
        self.library.update(clip, favorite=not clip.favorite)
        self.window.groups_changed()

    def set_favorites(self, clips: list[Clip], on: bool) -> None:
        for c in clips:
            self.library.update(c, favorite=on)
        self.window.toast.show_message(tr("Added to favorites") if on else tr("Removed from favorites"))
        self.window.groups_changed()

    def set_game(self, clips: list[Clip], name: str, appid: str) -> None:
        appid = (appid or games.appid_for(name) or "") if name else ""
        for c in clips:
            self.library.update(c, game=name, appid=appid)
        self.window.toast.show_message(tr("Game set: {name}", name=name) if name else tr("Game removed"))
        self.window.groups_changed()

    def rename(self, clip: Clip, text: str) -> None:
        old = clip.file
        try:
            self.library.rename(clip, text)
        except FileExistsError:
            self.window.warn(tr("Rename"), tr("A clip already has this name."))
            return
        except OSError as exc:
            self.window.warn(tr("Rename"), tr("Could not rename: {error}", error=exc))
            return
        self.window.clip_renamed(old, clip)

    # ------------------------------------------------------------ trash
    def trash(self, clips: list[Clip]) -> None:
        """Trash that can be undone for a few seconds (then the system trash)."""
        if not clips:
            return
        token = self.library.trash(clips)
        self.window.clips_removed(clips)
        n = len(clips)
        text = tr('"{title}" moved to the trash', title=clips[0].title) if n == 1 else \
            tr("{n} clips moved to the trash", n=n)
        self.window.toast.show_message(text, tr("Undo"), lambda: self.undo_trash(token), ms=7000)

    def undo_trash(self, token: str) -> None:
        n = self.library.undo_trash(token)
        self.window.groups_changed()
        self.window.toast.show_message(tr("Clip restored") if n == 1 else tr("{n} clips restored", n=n) if n else
                                       tr("Could not restore: look in the system trash"))

    def enforce_quota(self) -> None:
        removed = self.library.enforce_quota(self.settings.max_storage_gb)
        if removed:
            self.window.clips_removed(removed)

    # ------------------------------------------------------------ sound, copy
    def copy_audio(self, clip: Clip) -> None:
        try:
            copy_files([media.audio_mp3(self.library.path(clip), 0)])
            self.window.toast.show_message(tr("Clip sound copied as MP3: paste it with Ctrl+V"))
        except Exception as exc:
            self.window.toast.show_message(tr("Could not extract the sound: {error}", error=exc))

    # ------------------------------------------------------------ quick export for Discord
    def quick_export(self, clips: list[Clip], screen: int | None | str = "auto") -> None:
        """Discord preset, without opening the editor. screen: "auto" (multicam edit if there is one,
        otherwise screen 1), None (multicam edit) or k (screen k alone). The cuts and the mix saved
        in the editor are applied."""
        for c in clips:
            self._quick_queue.append((c, screen))
        self._quick_total += len(clips)
        if not self._quick_busy:
            self._next_quick()

    def _next_quick(self) -> None:
        if not self._quick_queue:
            self._quick_busy = False
            total, ok = self._quick_total, self._quick_ok
            self._quick_total = self._quick_ok = 0
            if total > 1:
                self.window.toast.show_message(tr("{ok}/{total} exported for Discord (Exports folder)", ok=ok, total=total))
            return
        self._quick_busy = True
        clip, screen = self._quick_queue.pop(0)
        s = self.settings
        edit = clip.edit or {}
        t_in, t_out = edit.get("range", [0.0, clip.duration])
        segments = E.kept_segments(t_in, t_out, [tuple(c) for c in edit.get("cuts", [])])
        kinds = [t.get("kind", "other") for _stream, t in clip.separate_tracks()]
        tracks = [{"stream": stream, **m} for (stream, _t), m in
                  zip(clip.separate_tracks(), E.track_mix(edit, kinds, s.levels))]
        dur = sum(b - a for a, b in segments)
        h, fps = E.discord_settings(dur, s.discord_limit_mb, clip.height or 1080, clip.fps or 60)
        plan, tag = None, ""
        if clip.n_screens > 1:
            if screen == "auto":
                screen = None if len(clip.angles) > 1 else 0
            plan = E.video_plan(segments, clip.angles, screen)
            tag = "-" + (tr("multicam") if screen is None else tr("screen{n}", n=screen + 1))
        size = tr("{n}mb", n=s.discord_limit_mb)
        out = self.library.exports_dir / f"{clip.stem}-{h or clip.height}p{fps or clip.fps}-{size}{tag}.mp4"
        spec = media.ExportSpec(src=str(self.library.path(clip)), out=str(out), segments=segments, tracks=tracks,
                                height=h, fps=fps, target_mb=s.discord_limit_mb, normalize=s.normalize_audio,
                                plan=plan, n_screens=clip.n_screens, src_size=(clip.width or 1920, clip.height or 1080),
                                src_fps=clip.fps or 60, screen=screen or 0)
        self.window.toast.show_message(tr('Exporting "{title}" for Discord…', title=clip.title), ms=60_000)
        exp = media.Exporter(spec, s.export_encoder,
                             on_done=lambda ok, err: self._quick.done.emit(clip, ok, err, str(out)))
        exp.start()

    def _on_quick_done(self, clip: Clip, ok: bool, err: str, out: str) -> None:
        toast = self.window.toast
        if ok:
            self._quick_ok += 1
            p = Path(out)
            toast.show_message(tr('"{name}" ready · {size}', name=p.name, size=fmt_size(p.stat().st_size)), tr("Copy"),
                               lambda: (copy_files([p]), toast.show_message(tr("File copied: Ctrl+V in Discord"))),
                               ms=8000)
        else:
            toast.show_message(tr("Export failed: {error}", error=err[:140]))
        self._next_quick()

    # ------------------------------------------------------------ thumbnails and previews
    def load_thumbs(self, clips: list) -> None:
        todo = [c for c in clips if c.file not in self._thumbs_pending]
        if not todo:
            return
        self._thumbs_pending.update(c.file for c in todo)
        job = ThumbJob(self.library, todo)
        job.signals.done.connect(self._on_thumb)
        self.media_pool.start(job)

    def _on_thumb(self, clip, path: str) -> None:
        self._thumbs_pending.discard(clip.file)
        pm = QPixmap(path) if path else QPixmap()
        if not pm.isNull():
            self.window.set_thumbnail(clip.file, pm)

    def load_sprite(self, clip) -> None:
        if clip.file in self._sprites_pending:
            return
        self._sprites_pending.add(clip.file)
        job = ThumbJob(self.library, [clip], sprite=True)
        job.signals.done.connect(self._on_hover_media)
        self.media_pool.start(job)

    def _on_hover_media(self, clip, path: str) -> None:
        if not path:
            return
        if path.endswith(".mp4"):
            self._sprites_pending.discard(clip.file)
            self.window.set_preview(clip.file, path)
        else:
            self.window.set_sprite(clip.file, QPixmap(path))
