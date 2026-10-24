"""Engine tests (no UI). Run: python -m unittest discover tests

The integration tests use a synthetic video/audio source (testsrc2 + beep) instead of the screen:
they also run in CI and on Linux.
"""

import os
import shutil
import sys
import tempfile
import time
import unittest
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
os.environ.setdefault("SNAPPY_LANG", "en")   # labels checked below are the English ones

from snappy.services import ffmpeg as ff  # noqa: E402
from snappy.models import edit  # noqa: E402
from snappy.services import media  # noqa: E402
from snappy.models.settings import Settings, audio_tracks  # noqa: E402
from snappy.models.library import ClipLibrary  # noqa: E402
from snappy.services.recorder import RecorderError, ReplayBuffer, Segment  # noqa: E402
from snappy.platforms.windows.hotkeys import MOD_ALT, MOD_CONTROL, MOD_NOREPEAT, MOD_SHIFT, parse_hotkey  # noqa: E402


def make_video(path: Path, seconds: float, tracks: int = 1, screens: int = 1) -> None:
    args = [ff.ffmpeg(), "-hide_banner", "-loglevel", "error", "-y"]
    for k in range(screens):
        args += ["-f", "lavfi", "-i", f"testsrc2=size=640x360:rate=30:d={seconds}"]
    for i in range(tracks):
        args += ["-f", "lavfi", "-i", f"sine=frequency={440 + 220 * i}:duration={seconds}"]
    for k in range(screens + tracks):
        args += ["-map", f"{k}"]
    args += ["-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest", str(path)]
    ff.run(args)


class TestSegmentSelection(unittest.TestCase):
    def segs(self, n, start=0.0, dur=1.0):
        return [Segment(Path(f"s{i}.ts"), start + i * dur, start + (i + 1) * dur) for i in range(n)]

    def test_takes_latest_within_limit(self):
        chosen = ReplayBuffer.select_segments(self.segs(40), 30)
        self.assertEqual(len(chosen), 30)
        self.assertEqual(chosen[-1].path.name, "s39.ts")
        self.assertLessEqual(sum(s.duration for s in chosen), 30.05)

    def test_shorter_buffer(self):
        chosen = ReplayBuffer.select_segments(self.segs(4), 30)
        self.assertEqual(len(chosen), 4)

    def test_never_exceeds_limit_with_uneven_segments(self):
        segs = [Segment(Path(f"s{i}.ts"), i * 1.0167, (i + 1) * 1.0167) for i in range(50)]
        chosen = ReplayBuffer.select_segments(segs, 10)
        self.assertLessEqual(sum(s.duration for s in chosen), 10.05)
        self.assertEqual(len(chosen), 9)

    def test_stops_at_gap(self):
        segs = self.segs(5) + self.segs(3, start=100)
        chosen = ReplayBuffer.select_segments(segs, 30)
        self.assertEqual([s.start for s in chosen], [100, 101, 102])


class TestHotkey(unittest.TestCase):
    def test_parse(self):
        self.assertEqual(parse_hotkey("Alt+F10"), (MOD_ALT | MOD_NOREPEAT, 0x79))
        self.assertEqual(parse_hotkey("ctrl + shift + s"),
                         (MOD_CONTROL | MOD_SHIFT | MOD_NOREPEAT, ord("S")))
        self.assertEqual(parse_hotkey("Win+Num5")[1], 0x65)

    def test_invalid(self):
        for bad in ("", "Alt", "Hyper+F1", "Ctrl+F99"):
            with self.assertRaises(ValueError):
                parse_hotkey(bad)


@unittest.skipUnless(sys.platform == "win32", "Windows start command")
class TestWindowsLaunch(unittest.TestCase):
    """Start at login and run.bat: the base Python's pythonw.exe (never a console), with the venv's
    packages and the source folder in front of the path, from any current folder."""

    def test_command_without_console(self):
        import subprocess
        from snappy import SOURCE_ROOT
        from snappy.platforms.windows.system import launch_command

        cmd = launch_command(["--minimized"])
        self.assertEqual(Path(cmd[0]).name.lower(), "pythonw.exe")
        self.assertNotEqual(Path(cmd[0]).parent, Path(sys.executable).parent)   # not the venv launcher
        self.assertEqual((cmd[1], cmd[2], cmd[-1]), ("-s", "-c", "--minimized"))
        # Same bootstrap, run with the console python.exe next to it: Snappy and its packages are found.
        probe = cmd[3].replace("from snappy.app import main; sys.exit(main())",
                               "import snappy, PySide6, numpy; print(snappy.__file__)")
        out = subprocess.run([str(Path(cmd[0]).with_name("python.exe")), "-s", "-c", probe], capture_output=True,
                             text=True, cwd=os.environ.get("SystemRoot", r"C:\Windows"), timeout=60)
        self.assertEqual(out.returncode, 0, out.stderr)
        self.assertEqual(Path(out.stdout.strip()), SOURCE_ROOT / "snappy" / "__init__.py")


class TestSettings(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        os.environ["APPDATA"] = self.dir
        os.environ["XDG_CONFIG_HOME"] = self.dir

    def tearDown(self):
        shutil.rmtree(self.dir, ignore_errors=True)

    def test_roundtrip_and_validation(self):
        s = Settings()
        s.clip_seconds = 9999
        s.quality = "nope"
        s.extra_monitors = [0, 1, 1, -3]
        s.save()
        loaded = Settings.load()
        self.assertEqual(loaded.clip_seconds, 600)
        self.assertEqual(loaded.quality, "high")
        self.assertEqual(loaded.extra_monitors, [1])     # never the primary screen, no duplicates
        Settings.path().write_text('{"clip_seconds": "abc", "fps": 30}', encoding="utf-8")
        loaded = Settings.load()
        self.assertEqual(loaded.clip_seconds, 30)
        self.assertEqual(loaded.fps, 30)

    def test_v1_migration(self):
        Settings.path().write_text('{"encoder": "h264_amf", "microphone_volume": 1.5}', encoding="utf-8")
        loaded = Settings.load()
        # AMF only exists on Windows: elsewhere, back to automatic.
        self.assertEqual(loaded.encoder, "amf" if sys.platform == "win32" else "auto")
        self.assertEqual(loaded.levels["mic"], 1.5)

    def test_audio_tracks(self):
        s = Settings()
        s.capture_microphone, s.chat_app = True, "Discord.exe"
        self.assertEqual([(t["kind"], t["source"]) for t in audio_tracks(s)],
                         [("game", "exclude:Discord.exe"), ("chat", "app:Discord.exe"), ("mic", "mic:")])
        s.capture_chat = False
        s.game_mode = "all"
        self.assertEqual([t["source"] for t in audio_tracks(s)], ["all", "mic:"])
        s.split_apps, s.split_app_list = True, ["Spotify.exe", "spotify.exe", "chrome.exe"]
        tracks = audio_tracks(s)
        self.assertEqual([t["label"] for t in tracks], ["Other sounds", "Spotify", "chrome", "Microphone"])
        self.assertEqual(tracks[0]["source"], "exclude:Spotify.exe,chrome.exe")
        s.game_mode, s.game_app = "app", "Game.exe"
        self.assertEqual(audio_tracks(s)[0]["source"], "app:Game.exe")
        # "Game" set to the chat app: the voice is not recorded twice, back to "all except the chat".
        s = Settings()
        s.game_mode, s.game_app, s.chat_app = "app", "discord.exe", "Discord.exe"
        self.assertEqual([t["source"] for t in audio_tracks(s)][:2], ["exclude:Discord.exe", "app:Discord.exe"])


class TestMontage(unittest.TestCase):
    def test_kept_segments(self):
        self.assertEqual(edit.kept_segments(1, 9, [(3, 4), (3.5, 5), (8.5, 12)]), [(1, 3), (5, 8.5)])
        self.assertEqual(edit.kept_segments(0, 5, []), [(0, 5)])

    def test_video_plan(self):
        segs = [(0.0, 2.0), (3.0, 8.0)]
        self.assertEqual(edit.video_plan(segs, [[0.0, 0], [4.0, 1], [6.0, 0]]),
                         [(0.0, 2.0, 0), (3.0, 4.0, 0), (4.0, 6.0, 1), (6.0, 8.0, 0)])
        self.assertEqual(edit.video_plan(segs, [[0.0, 0], [4.0, 1]], only=1), [(0.0, 2.0, 1), (3.0, 8.0, 1)])
        self.assertEqual(edit.angle_at([[0.0, 1], [2.0, 0]], 1.0), 1)

    def test_envelope(self):
        self.assertEqual(media.envelope_expr([], 0.8, 0), "0.8000")
        expr = media.envelope_expr([(1.0, 1.0), (3.0, 0.0)], 1.0, 0.5)
        self.assertIn("lt((t+0.5000),1.0000)", expr)

    def test_discord_settings(self):
        self.assertEqual(edit.discord_settings(30, 10, 1440, 60), (720, 0))   # 60 fps: already the original
        self.assertEqual(edit.discord_settings(120, 10, 1080, 60)[0], 480)
        self.assertEqual(edit.discord_settings(5, 10, 720, 30), (0, 0))   # already small enough


class TestLibrary(unittest.TestCase):
    def setUp(self):
        self.dir = Path(tempfile.mkdtemp())
        self.lib = ClipLibrary(self.dir)

    def tearDown(self):
        shutil.rmtree(self.dir, ignore_errors=True)

    def test_add_thumbnail_rename_delete(self):
        src = self.dir / "a.mp4"
        make_video(src, 4)
        clip = self.lib.add(src, datetime.now(), ff.probe_duration(src), app="Game")
        self.assertTrue(self.lib.thumb_path(clip).is_file())
        self.assertEqual((clip.width, clip.height, clip.fps), (640, 360, 30))
        self.assertEqual(self.lib.apps(), ["Game"])
        self.lib.rename(clip, "Mon: super clip")
        self.assertEqual(clip.file, "Mon- super clip.mp4")
        self.assertTrue((self.dir / clip.file).is_file())
        self.assertTrue(self.lib.thumb_path(clip).is_file())
        self.assertEqual(len(ClipLibrary(self.dir).clips()), 1)  # index persisted
        self.lib.delete(clip)
        self.assertFalse((self.dir / clip.file).exists())

    def test_trash_and_undo(self):
        a, b = self.dir / "a.mp4", self.dir / "b.mp4"
        make_video(a, 1)
        make_video(b, 1)
        ca = self.lib.add(a, datetime.now(), 1.0)
        cb = self.lib.add(b, datetime.now(), 1.0)
        token = self.lib.trash([ca, cb])
        self.assertEqual(self.lib.clips(), [])
        self.assertFalse(a.exists())
        self.assertEqual(self.lib.undo_trash(token), 2)
        self.assertTrue(a.exists() and b.exists())
        self.assertEqual(len(self.lib.clips()), 2)

    def test_filters_and_groups(self):
        for i, (game, fav) in enumerate((("Hades", True), ("Hades", False), ("", False))):
            p = self.dir / f"c{i}.mp4"
            make_video(p, 1)
            self.lib.add(p, datetime(2026, 1, 1, 12, i), 1.0, game=game, favorite=fav)
        self.assertEqual([g[:2] for g in self.lib.groups()], [("Hades", 2)])
        self.assertEqual(len(self.lib.clips(group="Hades")), 2)
        self.assertEqual(len(self.lib.clips(group="")), 1)
        self.assertEqual(len(self.lib.clips(favorites_only=True)), 1)
        self.assertEqual([c.file for c in self.lib.clips(sort="old")][0], "c0.mp4")
        self.assertEqual(len(self.lib.clips(day=(2026, 1, 1))), 3)

    def test_detects_files_added_by_hand(self):
        make_video(self.dir / "manuel.mp4", 1, tracks=3, screens=2)
        self.lib.reload()
        clip = self.lib.clips()[0]
        self.lib.ensure_metadata(clip)
        self.assertEqual(clip.title, "manuel")
        self.assertEqual(clip.n_screens, 2)
        self.assertEqual([t["kind"] for t in clip.tracks], ["game", "chat", "mic"])

    def test_quota_keeps_favorites(self):
        clips = []
        for i in range(3):
            p = self.dir / f"c{i}.mp4"
            make_video(p, 1)
            clips.append(self.lib.add(p, datetime(2026, 1, 1, 12, i), 1.0))
        self.lib.update(clips[0], favorite=True)
        limit_gb = (clips[0].size + clips[2].size + 1) / 1024 ** 3
        removed = self.lib.enforce_quota(limit_gb)
        self.assertEqual([c.file for c in removed], ["c1.mp4"])


class TestReplayBuffer(unittest.TestCase):
    """Integration: real FFmpeg process with a synthetic source."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def start_buffer(self, limit: int, audio: bool = True, screens: int = 1, tracks: int = 1):
        s = Settings()
        s.clip_seconds = limit
        s.fps = 30
        s.output_height = 0
        s.capture_system_audio = audio
        s.capture_chat = audio and tracks >= 2
        s.capture_microphone = audio and tracks >= 3
        s.extra_monitors = [1] if screens > 1 else []
        rb = ReplayBuffer(s, test_mode=True, buffer_dir=self.tmp / "buf")
        rb.start()
        deadline = time.time() + 20
        while rb.state != "recording" and time.time() < deadline:
            time.sleep(0.1)
        self.assertEqual(rb.state, "recording")
        return rb

    def run_buffer(self, limit: int, wait: float, **kw):
        rb = self.start_buffer(limit, **kw)
        try:
            time.sleep(wait)
            return rb.save_clip(self.tmp / "clips"), rb
        finally:
            rb.stop()

    def test_full_buffer_gives_fixed_length(self):
        clip, _ = self.run_buffer(limit=5, wait=8)
        self.assertLessEqual(clip.duration, 5.0 + 0.02)
        self.assertGreaterEqual(clip.duration, 4.0)
        streams = {s["codec_type"] for s in ff.probe(clip.path)["streams"]}
        self.assertEqual(streams, {"video", "audio"})
        self.assertFalse(clip.mix_track)

    def test_partial_buffer_is_shorter(self):
        clip, _ = self.run_buffer(limit=20, wait=3)
        self.assertLess(clip.duration, 20)
        self.assertGreater(clip.duration, 1)

    def test_disk_usage_is_bounded(self):
        _, rb = self.run_buffer(limit=5, wait=25, audio=False)
        self.assertLessEqual(len(list((self.tmp / "buf").glob("seg_*.ts"))), 5 + 15)

    def test_screens_and_tracks(self):
        rb = self.start_buffer(5, screens=2, tracks=3)
        try:
            time.sleep(7)
            clip = rb.save_clip(self.tmp / "clips")
        finally:
            rb.stop()
        info = ff.media_info(clip.path)
        # 2 screens; Mix first then the 3 separate tracks
        self.assertEqual((info["video_streams"], info["audio_streams"]), (2, 4))
        self.assertTrue(clip.mix_track)
        self.assertEqual([t["kind"] for t in clip.tracks], ["game", "chat", "mic"])
        self.assertLessEqual(clip.duration, 5.05)

    def test_manual_recording(self):
        """start / stop on top of the replay: everything in between is kept (longer than the replay),
        with both screens and every track, a clip can still be saved meanwhile, and the
        recording file next to the clips is cleaned up."""
        rb = self.start_buffer(3, screens=2, tracks=2)
        clips = self.tmp / "clips"
        try:
            time.sleep(1.5)
            rb.start_recording(clips)
            self.assertTrue(rb.is_recording)
            self.assertTrue(rb.can_record)
            time.sleep(3)
            replay = rb.save_clip(clips, name="replay")          # the ring keeps working
            self.assertLessEqual(replay.duration, 3.05)
            time.sleep(4)
            saved = rb.stop_recording(clips, name="manual")
            self.assertFalse(rb.is_recording)
            with self.assertRaises(RecorderError):
                rb.stop_recording(clips)
            # A recording interrupted by a buffer stop leaves nothing behind.
            rb.start_recording(clips)
            time.sleep(1.5)
        finally:
            rb.stop()
        self.assertFalse(rb.is_recording)
        self.assertGreater(saved.duration, 6.5)                   # ~7 s, not capped at the replay's 3 s
        self.assertLess(saved.duration, 9.5)
        info = ff.media_info(saved.path)
        self.assertEqual((info["video_streams"], info["audio_streams"]), (2, 3))   # Mix + 2 tracks
        self.assertTrue(saved.mix_track)
        self.assertEqual(sorted(p.name for p in clips.iterdir()), ["manual.mp4", "replay.mp4"])

    def test_restart_leaves_a_single_ffmpeg(self):
        import psutil

        buf = str(self.tmp / "buf").lower()

        def ffmpegs():
            return [p for p in psutil.process_iter(["name", "cmdline"])
                    if (p.info["name"] or "").lower().startswith("ffmpeg")
                    and any(buf in (a or "").lower() for a in p.info["cmdline"] or [])]

        rb = self.start_buffer(5, tracks=1)
        try:
            # Settings changed in a row, including a stop while FFmpeg is starting.
            for tracks in (2, 3, 2, 3):
                s = rb.settings.copy()
                s.capture_chat, s.capture_microphone = tracks >= 2, tracks >= 3
                rb.restart(s)
                time.sleep(0.3)
            deadline = time.time() + 20
            while rb.state != "recording" and time.time() < deadline:
                time.sleep(0.1)
            time.sleep(3)
            self.assertEqual(len(ffmpegs()), 1)
            clip = rb.save_clip(self.tmp / "clips")
            self.assertEqual(ff.media_info(clip.path)["audio_streams"], 4)   # Mix + 3 tracks
            # An orphan FFmpeg (Snappy crashed) is stopped at the next start.
            orphan = rb._proc
            rb._proc = None
            rb._running = False
            rb._watchdog.join(timeout=5)
            rb._running = False
            rb.start()
            deadline = time.time() + 20
            while rb.state != "recording" and time.time() < deadline:
                time.sleep(0.1)
            self.assertIsNotNone(orphan.poll())
            self.assertEqual(len(ffmpegs()), 1)
        finally:
            rb.stop()
        self.assertEqual(ffmpegs(), [])


class TestExport(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = Path(tempfile.mkdtemp())
        cls.src = cls.tmp / "src.mp4"
        make_video(cls.src, 6, tracks=2, screens=2)

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def spec(self, name, **kw):
        tracks = [{"stream": 0, "gain": 1.0, "muted": False, "points": []},
                  {"stream": 1, "gain": 0.5, "muted": False, "points": [(1.0, 1.0), (3.0, 0.0)]}]
        base = dict(src=str(self.src), out=str(self.tmp / name), segments=[(0.5, 2.0), (3.0, 5.5)], tracks=tracks,
                    n_screens=2, src_size=(640, 360), src_fps=30)
        base.update(kw)
        return media.ExportSpec(**base)

    def run_export(self, spec, pref="cpu"):
        ok, err = media.Exporter(spec, pref).run()
        self.assertTrue(ok, err)
        return ff.media_info(spec.out)

    def test_montage_with_cuts_and_normalisation(self):
        segs = [(0.5, 2.0), (3.0, 5.5)]
        plan = edit.video_plan(segs, [[0.0, 0], [4.0, 1]])
        info = self.run_export(self.spec("m.mp4", plan=plan, height=240, normalize=True))
        self.assertAlmostEqual(info["duration"], 4.0, delta=0.15)
        self.assertEqual((info["height"], info["video_streams"], info["audio_streams"]), (240, 1, 1))

    def test_target_size(self):
        info = self.run_export(self.spec("t.mp4", target_mb=0.3, screen=1))
        self.assertLessEqual(os.path.getsize(self.tmp / "t.mp4"), 0.3e6 * 1.05)
        self.assertEqual(info["video_streams"], 1)

    def test_original_copy_keeps_everything(self):
        info = self.run_export(self.spec("o.mp4", segments=[(1.0, 5.0)], copy=True))
        self.assertEqual((info["video_streams"], info["audio_streams"]), (2, 3))   # mix + 2 tracks

    def test_audio_only(self):
        info = self.run_export(self.spec("a.mp3", audio_only=True))
        self.assertEqual((info["video_streams"], info["audio_streams"]), (0, 1))

    def test_decode_and_peaks(self):
        arrays = media.decode_tracks(str(self.src), [0, 1])
        self.assertEqual(len(arrays), 2)
        self.assertEqual(arrays[0].shape[1], 2)
        self.assertGreater(float(media.peaks(arrays[0], 10).max()), 0.1)


if __name__ == "__main__":
    unittest.main(verbosity=2)
