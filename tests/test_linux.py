"""Tests of the Linux version that need neither Linux nor a graphics card: settings turned into
gpu-screen-recorder options, shortcuts, .desktop files, assembly of a replay.
Run: python -m unittest discover tests"""

import os
import shutil
import sys
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
        portal = self.engine(capture_method="portal")
        self.assertEqual(portal._targets, ["portal"])
        self.assertIn("-restore-portal-session", portal.build_command(0))

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


if __name__ == "__main__":
    unittest.main()
