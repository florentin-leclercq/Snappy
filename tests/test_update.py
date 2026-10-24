"""Updates from the GitHub releases (snappy/update.py), without reaching GitHub: a local HTTP server
stands in for its API, local git repositories for the clone. Run: python -m unittest discover tests"""

import hashlib
import http.server
import io
import json
import os
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
import threading
import time
import unittest
import unittest.mock
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from snappy import __version__, update  # noqa: E402
from snappy.platforms import generic, system  # noqa: E402

ZIP = r"windows.*\.zip$"     # asset of the test releases (Snappy.exe's, whatever the system running the tests)


class FakeGitHub:
    """Serves {path: (status, bytes)} on 127.0.0.1; SNAPPY_UPDATE_API points to it."""

    def __init__(self, routes: dict):
        self.routes = routes
        routes_ref = routes

        class Handler(http.server.BaseHTTPRequestHandler):
            def do_GET(self):
                status, body = routes_ref.get(self.path, (404, b'{"message": "Not Found"}'))
                self.send_response(status)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *args):
                pass

        self.server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self.server.server_port}"
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def close(self):
        self.server.shutdown()
        self.server.server_close()


def release_json(tag: str, assets: list[dict] | None = None) -> bytes:
    return json.dumps({"tag_name": tag, "name": f"Snappy {tag}", "body": "notes",
                       "html_url": f"https://github.com/o/r/releases/{tag}", "assets": assets or []}).encode()


class TestVersions(unittest.TestCase):
    def test_parse_and_compare(self):
        self.assertEqual(update.parse_version("v1.2.3"), (1, 2, 3))
        self.assertEqual(update.parse_version("0.4-beta"), (0, 4))
        self.assertEqual(update.parse_version("latest"), ())
        self.assertTrue(update.is_newer("v0.2.0", "0.1.9"))
        self.assertTrue(update.is_newer("1.0.1", "1.0"))
        self.assertFalse(update.is_newer("v1.0", "1.0.0"))
        self.assertFalse(update.is_newer("v0.9", "1.0"))
        self.assertFalse(update.is_newer("nightly", "1.0"))

    def test_set_version_script(self):
        """scripts/set_version.py: both files changed, line endings kept, only the version line."""
        sys.path.insert(0, str(ROOT / "scripts"))
        import set_version

        tmp = Path(tempfile.mkdtemp())
        try:
            (tmp / "snappy").mkdir()
            (tmp / "snappy" / "__init__.py").write_bytes(b'"""Doc."""\r\n__version__ = "0.1.0"\r\nAPP_NAME = "Snappy"\r\n')
            (tmp / "pyproject.toml").write_bytes(b'[project]\nversion = "0.1.0"\n[tool.x]\nversion = "9"\n')
            self.assertEqual(set_version.current(tmp), "0.1.0")
            self.assertEqual(set_version.next_patch("0.1.9"), "0.1.10")
            self.assertEqual(set_version.apply("0.2.0", tmp), ["snappy/__init__.py", "pyproject.toml"])
            self.assertEqual((tmp / "snappy" / "__init__.py").read_bytes(),
                             b'"""Doc."""\r\n__version__ = "0.2.0"\r\nAPP_NAME = "Snappy"\r\n')
            self.assertEqual((tmp / "pyproject.toml").read_bytes(), b'[project]\nversion = "0.2.0"\n[tool.x]\nversion = "9"\n')
            self.assertEqual(set_version.apply("0.2.0", tmp), [])
            for bad in ("0.2", "v0.2.0", "0.2.0-beta", ""):
                with self.assertRaises(ValueError):
                    set_version.apply(bad, tmp)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_package_and_pyproject_agree(self):
        text = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
        self.assertEqual(re.search(r'^version = "([^"]+)"', text, re.M).group(1), __version__)


class TestLatestRelease(unittest.TestCase):
    def setUp(self):
        self.gh = FakeGitHub({})
        env = {"SNAPPY_UPDATE_API": self.gh.url, "SNAPPY_UPDATE_REPO": "o/r"}
        self.env = unittest.mock.patch.dict(os.environ, env)
        self.env.start()

    def tearDown(self):
        self.env.stop()
        self.gh.close()

    def test_release_and_assets(self):
        self.gh.routes["/repos/o/r/releases/latest"] = (200, release_json("v2.1.0", [
            {"name": "Snappy-2.1.0-windows.zip", "browser_download_url": "http://x/w.zip", "size": 12,
             "digest": "sha256:ABCD"},
            {"name": "notes.txt", "browser_download_url": "http://x/n.txt", "size": 3}]))
        rel = update.latest_release()
        self.assertEqual((rel.tag, rel.version), ("v2.1.0", "2.1.0"))
        self.assertEqual(rel.asset(ZIP)["url"], "http://x/w.zip")
        self.assertEqual(rel.asset(ZIP)["sha256"], "abcd")
        self.assertIsNone(rel.asset(""))                  # a system without self-update

    def test_no_release_yet(self):
        self.assertIsNone(update.latest_release())

    def test_unreachable(self):
        self.gh.routes["/repos/o/r/releases/latest"] = (500, b"oops")
        with self.assertRaises(OSError):
            update.latest_release()
        with unittest.mock.patch.dict(os.environ, {"SNAPPY_UPDATE_API": "http://127.0.0.1:9"}):
            with self.assertRaises(OSError):
                update.latest_release(timeout=2)


@unittest.skipUnless(shutil.which("git"), "git is needed")
class TestSourceUpdate(unittest.TestCase):
    """A clone moves to the tag of the latest release, not to the tip of main."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.patches = [unittest.mock.patch.dict(os.environ, {
            "XDG_CACHE_HOME": str(self.tmp / "cache"), "LOCALAPPDATA": str(self.tmp / "cache"),
            "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t", "GIT_COMMITTER_NAME": "t",
            "GIT_COMMITTER_EMAIL": "t@t", "SNAPPY_NO_UPDATE": ""}),
            unittest.mock.patch.object(update, "_running_from", return_value=False)]
        for p in self.patches:
            p.start()
        self.origin = self.tmp / "origin.git"
        self.git(self.tmp, "init", "--quiet", "--bare", "-b", "main", str(self.origin))
        self.dev = self.tmp / "dev"
        self.git(self.tmp, "clone", "--quiet", str(self.origin), str(self.dev))
        self.git(self.dev, "checkout", "--quiet", "-B", "main")
        self.commit(self.dev, "app.txt", "1\n", "v1")
        self.git(self.dev, "tag", "v1.0.0")
        self.git(self.dev, "push", "--quiet", "origin", "main", "v1.0.0")
        self.user = self.tmp / "user"
        self.git(self.tmp, "clone", "--quiet", str(self.origin), str(self.user))
        # Release v1.1.0, then a commit on main not released yet.
        self.commit(self.dev, "app.txt", "2\n", "v1.1")
        self.git(self.dev, "tag", "v1.1.0")
        self.commit(self.dev, "app.txt", "3\n", "work in progress")
        self.git(self.dev, "push", "--quiet", "origin", "main", "v1.1.0")

    def tearDown(self):
        for p in self.patches:
            p.stop()
        shutil.rmtree(self.tmp, ignore_errors=True)

    @staticmethod
    def git(cwd: Path, *args: str) -> str:
        return subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True).stdout.strip()

    def commit(self, repo: Path, name: str, text: str, message: str) -> None:
        (repo / name).write_text(text)
        self.git(repo, "add", name)
        self.git(repo, "commit", "--quiet", "-m", message)

    def head(self, repo: Path) -> str:
        return self.git(repo, "rev-parse", "HEAD")

    def tag(self, name: str) -> str:
        return self.git(self.dev, "rev-parse", f"{name}^{{commit}}")

    def test_moves_to_the_release_tag(self):
        result = update.update_source(self.user, update.Release("v1.1.0"))
        self.assertEqual(result, "updated")
        self.assertEqual(self.head(self.user), self.tag("v1.1.0"))          # not the newer main
        self.assertEqual((self.user / "app.txt").read_text(), "2\n")
        self.assertEqual(update.update_source(self.user, update.Release("v1.1.0")), "up to date")
        self.assertIn("-> v1.1.0", (self.tmp / "cache" / "Snappy" / "update.log").read_text())

    def test_newer_clone_is_left_alone(self):
        self.git(self.user, "pull", "--quiet", "origin", "main")             # ahead of the release
        before = self.head(self.user)
        self.assertEqual(update.update_source(self.user, update.Release("v1.1.0")), "up to date")
        self.assertEqual(self.head(self.user), before)

    def test_local_changes_block_the_update(self):
        (self.user / "app.txt").write_text("edited\n")
        before = self.head(self.user)
        self.assertEqual(update.update_source(self.user, update.Release("v1.1.0")), "local changes")
        self.assertEqual(self.head(self.user), before)

    def test_local_commits_are_replayed(self):
        self.commit(self.user, "mine.txt", "x\n", "my change")
        self.assertEqual(update.update_source(self.user, update.Release("v1.1.0")), "updated")
        self.assertEqual(self.git(self.user, "rev-parse", "HEAD~1"), self.tag("v1.1.0"))
        self.assertEqual((self.user / "mine.txt").read_text(), "x\n")

    def test_conflicting_commits_are_kept(self):
        self.commit(self.user, "app.txt", "mine\n", "my change")
        before = self.head(self.user)
        self.assertEqual(update.update_source(self.user, update.Release("v1.1.0")), "conflict")
        self.assertEqual(self.head(self.user), before)
        self.assertEqual(self.git(self.user, "status", "--porcelain"), "")

    def test_failed_dependencies_roll_back(self):
        self.commit(self.dev, "requirements.txt", "./this-project-does-not-exist\n", "needs a package")
        self.git(self.dev, "tag", "v1.2.0")
        self.git(self.dev, "push", "--quiet", "origin", "v1.2.0")
        before = self.head(self.user)
        with unittest.mock.patch.object(update, "_venv_python", return_value=Path(sys.executable)):
            self.assertEqual(update.update_source(self.user, update.Release("v1.2.0")), "dependencies")
        self.assertEqual(self.head(self.user), before)

    def test_unknown_tag_and_disabled(self):
        self.assertEqual(update.update_source(self.user, update.Release("v9.9.9")), "offline")
        with unittest.mock.patch.dict(os.environ, {"SNAPPY_NO_UPDATE": "1"}):
            self.assertEqual(update.update_source(self.user, update.Release("v1.1.0")), "disabled")
        self.assertEqual(update.update_source(self.tmp, update.Release("v1.1.0")), "not a git clone")


class TestArchiveUpdate(unittest.TestCase):
    """Sources installed from the release archive (Linux, not a git clone): replaced by the new
    archive, .venv and files of your own kept, the previous version back if something fails."""

    PATTERN = r"linux.*\.tar\.gz$"

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.patches = [unittest.mock.patch.dict(os.environ, {"XDG_CACHE_HOME": str(self.tmp / "cache"),
                                                              "LOCALAPPDATA": str(self.tmp / "cache"),
                                                              "SNAPPY_NO_UPDATE": ""}),
                        unittest.mock.patch.object(update.system, "SOURCE_ASSET", self.PATTERN),
                        unittest.mock.patch.object(update, "_running_from", return_value=False)]
        for p in self.patches:
            p.start()
        self.gh = FakeGitHub({})
        self.root = self.tmp / "Snappy"
        self.write_tree(self.root, "1.0.0", {"snappy/old_module.py": "old", "requirements.txt": "numpy\n",
                                             "run.sh": "#!/bin/sh\necho old\n"})
        (self.root / ".venv").mkdir()
        (self.root / ".venv" / "keep").write_text("venv")
        (self.root / "mine.txt").write_text("my notes")

    def tearDown(self):
        for p in self.patches:
            p.stop()
        self.gh.close()
        shutil.rmtree(self.tmp, ignore_errors=True)

    @staticmethod
    def write_tree(root: Path, version: str, files: dict) -> None:
        (root / "snappy").mkdir(parents=True, exist_ok=True)
        (root / "snappy" / "__init__.py").write_text(f'"""Snappy."""\n__version__ = "{version}"\n')
        for name, text in files.items():
            (root / name).parent.mkdir(parents=True, exist_ok=True)
            (root / name).write_text(text)

    def release(self, version: str, files: dict, with_asset: bool = True) -> update.Release:
        src = self.tmp / f"build-{version}" / f"Snappy-{version}"
        self.write_tree(src, version, {"run.sh": "#!/bin/sh\n", **files})
        (src / "run.sh").chmod(0o755)
        archive = self.tmp / f"Snappy-{version}-linux.tar.gz"
        with tarfile.open(archive, "w:gz") as tar:
            tar.add(src, arcname=src.name)
        data = archive.read_bytes()
        self.gh.routes[f"/{archive.name}"] = (200, data)
        assets = [{"name": archive.name, "url": f"{self.gh.url}/{archive.name}", "size": len(data),
                   "sha256": hashlib.sha256(data).hexdigest()}] if with_asset else []
        return update.Release(f"v{version}", assets=assets)

    def test_archive_replaces_the_sources(self):
        rel = self.release("1.1.0", {"snappy/new_module.py": "new", "requirements.txt": "numpy\n",
                                     "run.sh": "#!/bin/sh\necho new\n"})
        self.assertEqual(update.update_source(self.root, rel), "updated")
        self.assertEqual(update.installed_version(self.root), "1.1.0")
        self.assertTrue((self.root / "snappy" / "new_module.py").exists())
        self.assertFalse((self.root / "snappy" / "old_module.py").exists())      # the package replaced whole
        self.assertEqual((self.root / ".venv" / "keep").read_text(), "venv")      # kept
        self.assertEqual((self.root / "mine.txt").read_text(), "my notes")
        self.assertIn("echo new", (self.root / "run.sh").read_text())
        if sys.platform != "win32":
            self.assertTrue(os.access(self.root / "run.sh", os.X_OK))
        self.assertFalse((self.root / ".update-backup").exists())
        self.assertEqual(update.update_source(self.root, rel), "up to date")

    def test_failed_dependencies_put_the_old_version_back(self):
        rel = self.release("1.2.0", {"snappy/new_module.py": "new",
                                     "requirements.txt": "./this-project-does-not-exist\n"})
        with unittest.mock.patch.object(update, "_venv_python", return_value=Path(sys.executable)):
            self.assertEqual(update.update_source(self.root, rel), "dependencies")
        self.assertEqual(update.installed_version(self.root), "1.0.0")
        self.assertTrue((self.root / "snappy" / "old_module.py").exists())
        self.assertFalse((self.root / "snappy" / "new_module.py").exists())
        self.assertEqual((self.root / "requirements.txt").read_text(), "numpy\n")
        self.assertEqual((self.root / ".venv" / "keep").read_text(), "venv")
        self.assertFalse((self.root / ".update-backup").exists())

    def test_without_archive_or_without_support(self):
        self.assertEqual(update.update_source(self.root, self.release("1.3.0", {}, with_asset=False)), "no archive")
        self.assertEqual(update.installed_version(self.root), "1.0.0")
        with unittest.mock.patch.object(update.system, "SOURCE_ASSET", ""):       # Windows: git clones only
            self.assertEqual(update.update_source(self.root, self.release("1.3.0", {})), "not a git clone")


class TestSelfUpdate(unittest.TestCase):
    """Snappy.exe: the release zip is downloaded, checked, unpacked, then installed after Snappy quits."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.env = unittest.mock.patch.dict(os.environ, {"LOCALAPPDATA": str(self.tmp / "cache"),
                                                         "XDG_CACHE_HOME": str(self.tmp / "cache")})
        self.env.start()
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as z:
            z.writestr(f"Snappy/{update.EXE_NAME}", self.exe_bytes())
            z.writestr("Snappy/_internal/new.txt", "new version")
        self.zip = buf.getvalue()
        self.gh = FakeGitHub({"/w.zip": (200, self.zip)})

    def tearDown(self):
        self.gh.close()
        self.env.stop()
        shutil.rmtree(self.tmp, ignore_errors=True)

    @staticmethod
    def exe_bytes() -> bytes:
        """A real program that starts and quits right away (the relaunched "Snappy.exe")."""
        if sys.platform == "win32":
            return Path(os.environ.get("SystemRoot", r"C:\Windows"), "System32", "hostname.exe").read_bytes()
        return b"MZ"

    def release(self, sha256: str | None = None) -> update.Release:
        digest = hashlib.sha256(self.zip).hexdigest() if sha256 is None else sha256
        return update.Release("v99.0.0", assets=[{"name": "Snappy-99.0.0-windows.zip", "url": f"{self.gh.url}/w.zip",
                                                  "size": len(self.zip), "sha256": digest}])

    def test_download_and_stage(self):
        seen = []
        folder = update.download_release(self.release(), progress=seen.append, pattern=ZIP)
        self.assertEqual(seen[-1], 1.0)
        self.assertTrue((folder / "_internal" / "new.txt").exists())
        self.assertEqual(update.staged_update(), ("99.0.0", folder))
        self.assertEqual(list(update.staging_dir().glob("*.zip")), [])        # archive removed

    def test_checksum_mismatch_is_rejected(self):
        with self.assertRaises(OSError):
            update.download_release(self.release(sha256="0" * 64), pattern=ZIP)
        self.assertIsNone(update.staged_update())

    def test_release_without_windows_zip(self):
        with self.assertRaises(OSError):
            update.download_release(update.Release("v99.0.0"), pattern=ZIP)

    def test_platform_contract(self):
        """Only Windows installs its own updates (Snappy.exe); elsewhere, git clones (the launcher)."""
        self.assertEqual(system.SELF_UPDATE, sys.platform == "win32")
        self.assertFalse(generic.SELF_UPDATE)
        self.assertFalse(generic.install_update(self.tmp / "Snappy", self.tmp, "1", 0, []))
        if sys.platform == "win32":
            self.assertRegex("Snappy-1.0.0-windows.zip", system.UPDATE_ASSET)
            self.assertTrue(system.long_path(self.tmp).startswith("\\\\?\\"))
        else:
            update.download_release(self.release(), pattern=ZIP)
            self.assertFalse(update.apply_staged(os.getpid()))         # nothing can install it here

    @unittest.skipUnless(sys.platform == "win32", "Snappy.exe")
    def test_new_exe_started_as_installer(self):
        """apply_staged starts the downloaded Snappy.exe with --install-update (it outlives Snappy)."""
        folder = update.download_release(self.release(), pattern=ZIP)
        with unittest.mock.patch("subprocess.Popen") as popen:
            self.assertTrue(update.apply_staged(1234, ["--minimized"], target=self.tmp / "install"))
        cmd = popen.call_args.args[0]
        self.assertEqual(Path(cmd[0]), folder / update.EXE_NAME)
        self.assertEqual(cmd[1:], ["--install-update", str(self.tmp / "install"), "--update-version", "99.0.0",
                                   "--wait-pid", "1234", "--relaunch", '["--minimized"]'])
        self.assertTrue(popen.call_args.kwargs["creationflags"] & subprocess.CREATE_NEW_PROCESS_GROUP)

    def test_can_replace(self):
        target = self.tmp / "install"
        target.mkdir()
        self.assertTrue(update.can_replace(target))

    def make_install(self) -> Path:
        target = self.tmp / "install" / "Snappy"
        (target / "_internal").mkdir(parents=True)
        (target / update.EXE_NAME).write_bytes(b"old exe")
        (target / "_internal" / "old.txt").write_text("old version")
        return target

    def test_install_folder(self):
        """The new folder replaces the old one once the old Snappy has quit, with its progress; the
        old one waits aside (removed by the installed Snappy), the update is not installed twice."""
        target = self.make_install()
        source = update.download_release(self.release(), pattern=ZIP)
        snappy = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(1.5)"])   # "Snappy" quitting
        seen = []
        start = time.time()
        self.assertTrue(update.install_folder(source, target, "99.0.0", snappy.pid,
                                              lambda text, f=None: seen.append((text, f))))
        self.assertGreaterEqual(time.time() - start, 1.0)                 # waited for the old Snappy
        self.assertIsNotNone(snappy.poll())
        self.assertTrue((target / "_internal" / "new.txt").exists())
        self.assertFalse((target / "_internal" / "old.txt").exists())
        self.assertEqual((target / update.EXE_NAME).read_bytes(), self.exe_bytes())
        fractions = [f for _t, f in seen if f is not None]
        self.assertEqual(fractions, sorted(fractions))
        self.assertAlmostEqual(fractions[-1], 1.0)
        self.assertIn("installed 99.0.0", (update.cache_root() / "update.log").read_text())
        self.assertIsNone(update.staged_update())                         # not installed twice
        with unittest.mock.patch.object(update, "install_dir", return_value=target):
            update.cleanup_after_install()
        self.assertFalse(target.with_name("Snappy.old").exists())
        self.assertFalse(update.staging_dir().exists())

    def test_failed_copy_puts_the_old_version_back(self):
        target = self.make_install()
        source = update.download_release(self.release(), pattern=ZIP)
        calls = []

        def copy_then_fail(src, dst, *a, **k):
            calls.append(src)
            if len(calls) == 2:
                raise OSError("disk full")
            return shutil.copyfile(src, dst)

        with unittest.mock.patch.object(update.shutil, "copy2", copy_then_fail):
            self.assertFalse(update.install_folder(source, target, "99.0.0"))
        self.assertEqual((target / "_internal" / "old.txt").read_text(), "old version")
        self.assertEqual((target / update.EXE_NAME).read_bytes(), b"old exe")
        self.assertFalse(target.with_name("Snappy.old").exists())
        self.assertIn("previous version put back", (update.cache_root() / "update.log").read_text())
        self.assertIsNone(update.staged_update())                         # not retried at every start


class TestProgressWindow(unittest.TestCase):
    """run_with_progress: no window while nothing is installed; the window with the steps otherwise."""

    def test_no_window_when_up_to_date(self):
        with unittest.mock.patch.dict(sys.modules, {"snappy.views.update_window": None}):
            self.assertEqual(update.run_with_progress(lambda progress: "up to date"), "up to date")

    def test_terminal_without_window(self):
        with unittest.mock.patch("snappy.views.update_window.can_show", return_value=False), \
                unittest.mock.patch("sys.stderr", new_callable=io.StringIO) as err:
            def task(progress):
                progress("Downloading", 0.5)
                progress("Downloading", 1.0)
                progress("Installing", None)
                return "updated"

            self.assertEqual(update.run_with_progress(task), "updated")
        self.assertEqual(err.getvalue().splitlines(), ["Downloading", "Installing"])

    def test_window_shows_the_steps(self):
        from PySide6.QtWidgets import QApplication

        from snappy.views import update_window

        seen = []
        real = update_window.UpdateWindow.set_progress

        def spy(window, text, fraction):
            seen.append((text, fraction, window.isVisible(), window.bar.maximum()))
            real(window, text, fraction)

        with unittest.mock.patch.dict(os.environ, {"QT_QPA_PLATFORM": os.environ.get("QT_QPA_PLATFORM", "offscreen")}), \
                unittest.mock.patch.object(update_window.UpdateWindow, "set_progress", spy):
            def task(progress):
                for f in (0.0, 0.5, 1.0):
                    progress("Downloading Snappy 9.9.9…", f)
                    time.sleep(0.15)
                progress("Installing Snappy 9.9.9…", None)
                time.sleep(0.15)
                return "updated"

            self.assertEqual(update.run_with_progress(task), "updated")
        self.assertIsNotNone(QApplication.instance())
        self.assertEqual(seen[0][:2], ("Downloading Snappy 9.9.9…", 0.0))
        self.assertIn(("Downloading Snappy 9.9.9…", 0.5, True, 1000), seen)
        self.assertEqual(seen[-1][:2], ("Installing Snappy 9.9.9…", None))     # moving bar


if __name__ == "__main__":
    unittest.main()
