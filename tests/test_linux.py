"""Tests of the Linux version that need neither Linux nor a graphics card: settings turned into
gpu-screen-recorder options, shortcuts, .desktop files, assembly of a replay.
Run: python -m unittest discover tests"""

import os
import json
import shutil
import socket
import sys
import threading
import tempfile
import unittest
import unittest.mock
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
os.environ.setdefault("SNAPPY_LANG", "en")   # labels checked below are the English ones

from snappy.models.settings import Settings, audio_tracks  # noqa: E402
from snappy.platforms.linux import capture, hotkeys  # noqa: E402
from snappy.platforms.linux import system as linux_system  # noqa: E402
from snappy.platforms.linux.replay import GsrReplayBuffer  # noqa: E402
from snappy.services import ffmpeg as ff  # noqa: E402

MONITORS = {0: {"geometry": (0, 0, 2560, 1440), "label": "Screen 1", "name": "DP-1"},
            1: {"geometry": (2560, 0, 1920, 1080), "label": "Screen 2", "name": "HDMI-A-1"}}


class TestAudioSources(unittest.TestCase):
    def test_gsr_arguments(self):
        self.assertEqual(capture.gsr_audio_arg("all"), "default_output")
        self.assertEqual(capture.gsr_audio_arg("exclude:vesktop,spotify"), "app-inverse:vesktop|app-inverse:spotify")
        self.assertEqual(capture.gsr_audio_arg("app:vesktop"), "app:vesktop")
        self.assertEqual(capture.gsr_audio_arg("mic:"), "default_input")
        self.assertEqual(capture.gsr_audio_arg("mic:alsa_input.usb-Yeti"), "device:alsa_input.usb-Yeti")

    def test_tracks_from_settings(self):
        s = Settings()
        s.chat_app, s.capture_microphone = "vesktop", True
        s.split_apps, s.split_app_list = True, ["Snappy"]       # Snappy's own sound on its track
        args = [capture.gsr_audio_arg(t["source"]) for t in audio_tracks(s)]
        self.assertEqual(args, ["app-inverse:vesktop|app-inverse:Snappy", "app:Snappy", "app:vesktop", "default_input"])

    def test_devices_checked(self):
        """gpu-screen-recorder rejects an unknown source: default_input missing, microphone unplugged."""
        devices = ["default_output", "alsa_output.usb-Generic.monitor",
                   "alsa_input.usb-Logitech_A50-00.mono-fallback", "alsa_input.pci.analog-stereo"]
        self.assertEqual(capture.resolve_audio_arg("default_input", devices), "device:alsa_input.pci.analog-stereo")
        self.assertEqual(capture.resolve_audio_arg("device:alsa_input.gone", devices),
                         "device:alsa_input.pci.analog-stereo")
        self.assertEqual(capture.resolve_audio_arg("default_output", devices), "default_output")
        self.assertEqual(capture.resolve_audio_arg("default_output", devices[1:]), "device:alsa_output.usb-Generic.monitor")
        self.assertEqual(capture.resolve_audio_arg("app-inverse:a|app-inverse:b", devices), "app-inverse:a|app-inverse:b")
        self.assertEqual(capture.resolve_audio_arg("default_input", ["default_input"] + devices), "default_input")
        self.assertEqual(capture.resolve_audio_arg("default_input", []), "default_input")   # list unavailable


class TestHotkeys(unittest.TestCase):
    def test_parse(self):
        self.assertEqual(hotkeys.parse_hotkey("Alt+F10"), (("Alt",), "F10"))
        self.assertEqual(hotkeys.parse_hotkey("ctrl + maj + s"), (("Ctrl", "Shift"), "s"))
        self.assertEqual(hotkeys.parse_hotkey("Win+Num5"), (("Super",), "KP_5"))
        for bad in ("", "Alt", "Hyper+F1", "Ctrl+F99"):
            with self.assertRaises(ValueError):
                hotkeys.parse_hotkey(bad)

    def test_kde_refusal_is_reported(self):
        """KDE returns the keys it kept: a refused key (already taken) becomes a visible error."""
        calls = []
        orig = hotkeys._Kde._call
        tmp = tempfile.mkdtemp()
        try:
            hotkeys._Kde._call = staticmethod(lambda method, *a: calls.append(method) or "([],)")
            with unittest.mock.patch.dict("os.environ", {"XDG_DATA_HOME": tmp}):
                with self.assertRaises(ValueError):
                    hotkeys._Kde.bind("save", "Ctrl+F9", ["snappy", "--hotkey", "save"])
                desktop = (Path(tmp) / "applications" / "snappy-save.desktop").read_text(encoding="utf-8")
                self.assertIn("X-KDE-Shortcuts=Ctrl+F9", desktop)
                code = hotkeys.qt_key_code("Ctrl+F9")
                hotkeys._Kde._call = staticmethod(lambda method, *a: f"([{code}],)")
                hotkeys._Kde.bind("save", "Ctrl+F9", ["snappy", "--hotkey", "save"])    # accepted
        finally:
            hotkeys._Kde._call = orig
            shutil.rmtree(tmp, ignore_errors=True)
        self.assertEqual(calls, ["doRegister", "setShortcut"])

    def test_desktop_formats(self):
        self.assertEqual(hotkeys.gnome_binding("Ctrl+Shift+F10"), "<Control><Shift>F10")
        self.assertEqual(hotkeys.qt_key_code("Alt+F10"), 0x08000000 | 0x01000039)   # Qt::ALT | Qt::Key_F10


class TestDesktopFiles(unittest.TestCase):
    def test_exec_quoting(self):
        line = linux_system.desktop_exec(["/home/a b/python", "-m", "snappy", "--x=50%"])
        self.assertEqual(line, '"/home/a b/python" -m snappy --x=50%%')

    def test_launch_command(self):
        args = linux_system.launch_args(["--hotkey", "save"])
        self.assertEqual(args[-2:], ["--hotkey", "save"])
        # run.sh from the install.sh environment (.venv), otherwise the current interpreter with the source folder
        self.assertTrue(args[0].endswith("run.sh") or any(a.startswith("PYTHONPATH=") for a in args), args)


class TestGsrReplay(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def engine(self, **changes) -> GsrReplayBuffer:
        s = Settings()
        s.chat_app, s.capture_microphone, s.extra_monitors = "vesktop", True, [1]
        for k, v in changes.items():
            setattr(s, k, v)
        rb = GsrReplayBuffer(s, MONITORS, buffer_dir=self.tmp / "buf")
        rb.screens, rb._targets = rb._screen_list()
        rb.tracks = audio_tracks(s)
        return rb

    def test_command(self):
        rb = self.engine(output_height=1080, quality="ultra", codec="hevc")
        main, extra = rb.build_command(0), rb.build_command(1)
        self.assertEqual(rb._targets, ["DP-1", "HDMI-A-1"])
        for opt, value in (("-w", "DP-1"), ("-s", "1920x1080"), ("-q", "ultra"), ("-k", "hevc"), ("-r", "30")):
            self.assertEqual(main[main.index(opt) + 1], value)
        self.assertEqual(main.count("-a"), 3)
        self.assertNotIn("-a", extra)
        self.assertNotIn("-s", extra)          # 1080p: nothing to scale down
        for cmd, k in ((main, 0), (extra, 1)):   # manual recording: command socket, aligned screens
            self.assertEqual(cmd[cmd.index("-ipc") + 1], str(rb.ipc_path(k)))
            self.assertEqual(cmd[cmd.index("-ro") + 1], str(rb.screen_dir(k)))
            self.assertIn("-write-first-frame-ts", cmd)
        self.assertNotIn("-write-first-frame-ts", self.engine(extra_monitors=[]).build_command(0))
        portal = self.engine(capture_method="portal")
        self.assertEqual(portal._targets, ["portal"])
        self.assertIn("-restore-portal-session", portal.build_command(0))

    def make_file(self, path: Path, dur: float, tracks: int) -> Path:
        args = [ff.ffmpeg(), "-v", "error", "-y", "-f", "lavfi", "-i", f"testsrc2=size=320x180:rate=30:d={dur}"]
        for i in range(tracks):
            args += ["-f", "lavfi", "-i", f"sine=frequency={400 + 200 * i}:duration={dur}"]
        args += ["-map", "0:v"] + [x for i in range(tracks) for x in ("-map", f"{i + 1}:a")]
        args += ["-c:v", "libx264", "-preset", "ultrafast", "-g", "30", "-pix_fmt", "yuv420p", "-c:a", "aac", str(path)]
        ff.run(args)
        return path

    def test_manual_recording(self):
        """start / stop through fake gpu-screen-recorder command sockets: the whole recording is
        kept (longer than the replay length) and screen 2, started 1.5 s later, is placed there."""
        rb = self.engine(clip_seconds=5, capture_microphone=False)
        rb._prepare_dir()
        sources = [self.make_file(self.tmp / "rec0.mp4", 8.0, 2), self.make_file(self.tmp / "rec1.mp4", 6.0, 0)]
        first_frame = [10_000_000, 11_500_000]     # µs: screen 2 starts 1.5 s after screen 1
        requests = []

        def serve(k: int, server: socket.socket) -> None:
            while True:
                try:
                    conn, _ = server.accept()
                except OSError:
                    return
                with conn:
                    name = json.loads(conn.recv(4096).decode())["name"]
                    requests.append((k, name))
                    if name == "stop-replay-recording":
                        out = rb.screen_dir(k) / f"Video_{k}.mp4"
                        shutil.copy(sources[k], out)
                        Path(f"{out}.ts").write_text(f"0 0 {first_frame[k]}\n")
                        with open(rb.buffer_dir / "saved.list", "a") as f:
                            f.write(f"{out}\n")
                    conn.sendall(json.dumps({"id": 1, "result": "ok"}).encode() + b"\n")

        servers = []
        for k in range(2):
            server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            server.bind(str(rb.ipc_path(k)))
            server.listen()
            servers.append(server)
            threading.Thread(target=serve, args=(k, server), daemon=True).start()
        alive = unittest.mock.Mock(poll=lambda: None)
        rb._procs, rb._running, rb._announced = [alive, alive], True, True
        try:
            rb.start_recording()
            self.assertTrue(rb.is_recording)
            saved = rb.stop_recording(self.tmp / "clips", name="manual")
        finally:
            for server in servers:
                server.close()
        self.assertFalse(rb.is_recording)
        self.assertEqual([n for _k, n in requests].count("start-replay-recording"), 2)
        self.assertEqual(requests[-1], (0, "stop-replay-recording"))   # main screen stopped last
        info = ff.media_info(saved.path)
        self.assertEqual((info["video_streams"], info["audio_streams"]), (2, 3))
        self.assertAlmostEqual(saved.duration, 8.0, delta=0.15)          # not capped at clip_seconds
        streams = ff.probe(saved.path)["streams"]
        starts = [float(s.get("start_time", 0)) for s in streams if s["codec_type"] == "video"]
        self.assertAlmostEqual(starts[1] - starts[0], 1.5, delta=0.1)
        self.assertEqual(list(rb.screen_dir(0).iterdir()), [])            # screen files cleaned up
        with self.assertRaises(Exception):
            rb.stop_recording(self.tmp / "clips")

    def test_assemble(self):
        """Screen 1 (2 tracks) of 6.5 s and screen 2 of 6.2 s -> clip of 5 s at most, ends aligned."""
        rb = self.engine(clip_seconds=5, capture_microphone=False)
        files = []
        for k, (dur, tracks) in enumerate(((6.5, 2), (6.2, 0))):
            f = self.tmp / f"screen{k}.mp4"
            args = [ff.ffmpeg(), "-v", "error", "-y", "-f", "lavfi", "-i", f"testsrc2=size=320x180:rate=30:d={dur}"]
            for i in range(tracks):
                args += ["-f", "lavfi", "-i", f"sine=frequency={400 + 200 * i}:duration={dur}"]
            args += ["-map", "0:v"] + [x for i in range(tracks) for x in ("-map", f"{i + 1}:a")]
            args += ["-c:v", "libx264", "-preset", "ultrafast", "-g", "30", "-pix_fmt", "yuv420p", "-c:a", "aac", str(f)]
            ff.run(args)
            files.append(f)
        out = self.tmp / "clip.mp4"
        tracks = rb.assemble(files, out, 5.0, rb.screens)
        info = ff.media_info(out)
        self.assertEqual(len(tracks), 2)
        self.assertEqual((info["video_streams"], info["audio_streams"]), (2, 3))
        self.assertEqual(info["audio_names"], ["Mix", "Game", "Voice chat"])
        self.assertLessEqual(ff.probe_duration(out), 5.05)
        self.assertGreater(ff.probe_duration(out), 4.0)


class TestEditorAudio(unittest.TestCase):
    """Long clips: the editor's tracks are memory-mapped instead of held in RAM, with the same data."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_spooled_decode_matches(self):
        import numpy as np
        from snappy.services import media

        clip = self.tmp / "clip.mp4"
        ff.run([ff.ffmpeg(), "-v", "error", "-y", "-f", "lavfi", "-i", "sine=frequency=440:duration=3",
                "-f", "lavfi", "-i", "sine=frequency=880:duration=3", "-map", "0:a", "-map", "1:a",
                "-ac", "1", "-c:a", "aac", str(clip)])
        in_ram = media.decode_tracks(str(clip), [0, 1])
        with unittest.mock.patch.object(media, "SPOOL_BYTES", 0), \
                unittest.mock.patch.object(media, "spool_dir", lambda: self.tmp / "spool"):
            spooled = media.decode_tracks(str(clip), [0, 1])
        self.assertIsInstance(spooled[0].base, np.memmap)
        for a, b in zip(in_ram, spooled):
            self.assertEqual(a.shape, b.shape)
            self.assertTrue(np.array_equal(a, b))
        self.assertEqual(list((self.tmp / "spool").glob("*.pcm")), [])   # file gone, mapping kept
        self.assertTrue(np.array_equal(media.peaks(spooled[1], 50), media.peaks(in_ram[1], 50)))

    def test_peaks_blocks(self):
        import numpy as np
        from snappy.services import media

        track = np.random.default_rng(0).integers(-32768, 32767, size=(10_001, 2), dtype=np.int16)
        mono = np.abs(track.astype(np.int32)).max(axis=1)
        for buckets in (1, 7, 300, 20_000):
            edges = np.linspace(0, len(mono), buckets + 1).astype(int)
            expected = np.maximum.reduceat(mono, np.minimum(edges[:-1], len(mono) - 1)) / 32768.0
            with unittest.mock.patch.object(media, "PEAK_BLOCK", 997):
                self.assertTrue(np.allclose(media.peaks(track, buckets), expected))


if __name__ == "__main__":
    unittest.main()
