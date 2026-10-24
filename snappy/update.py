"""Updates from the GitHub releases (not the latest commit): the same rules on Windows and Linux.

Two kinds of installation:

* From the sources (Linux `install.sh` or Windows `run.bat`): the launcher runs
  `python -m snappy.update` before Snappy. A git clone moves to the tag of the latest release
  (fast-forward; local commits are replayed on top of it, kept as they are if they conflict).
  Sources installed from the release archive of the system (`system.SOURCE_ASSET`: Linux
  `Snappy-<version>-linux.tar.gz`) are replaced by the new archive (.venv and files of your own
  are kept; the previous version is put back if something fails). Either way, the new Python
  dependencies are installed (otherwise back to the previous version).
* A packaged Snappy (`system.SELF_UPDATE`: Snappy.exe on Windows) downloads the release asset
  of its system (`system.UPDATE_ASSET`: `Snappy-<version>-windows.zip`) in the background,
  checks its SHA-256 when GitHub gives it, and unpacks it into the cache. The update is
  installed when Snappy restarts ("Restart to update" or the next start), by the system layer
  (`system.install_update`: platforms/windows/update.py).

It never stands in the way of the launch: offline, slow network (timeout), no release yet, local
edits not committed, Snappy already running from the folder... -> the current version runs as it
is. What happened is written to update.log in Snappy's cache folder.

    SNAPPY_NO_UPDATE=1        no update at all
    SNAPPY_UPDATE_REPO=o/r    GitHub repository followed (default: florentin-leclercq/Snappy)
    SNAPPY_UPDATE_API=url     other API root (tests)
    SNAPPY_UPDATE_TIMEOUT=8   seconds allowed to reach GitHub

The launcher runs this module before Snappy: it does not need Qt.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tarfile
import time
import urllib.error
import urllib.request
import zipfile
from dataclasses import dataclass, field
from pathlib import Path

from . import APP_NAME, SOURCE_ROOT, __version__
from .platforms import system

REPO = "florentin-leclercq/Snappy"
API = "https://api.github.com"
DEFAULT_TIMEOUT = 8.0
EXE_NAME = APP_NAME + system.EXE_SUFFIX


# --------------------------------------------------------------------------
# Versions and releases
# --------------------------------------------------------------------------
def parse_version(text: str) -> tuple[int, ...]:
    """'v1.2.3' -> (1, 2, 3); a suffix ('-beta') is ignored. () when it is not a version."""
    m = re.match(r"^\s*v?(\d+(?:\.\d+)*)", text or "")
    return tuple(int(p) for p in m.group(1).split(".")) if m else ()


def is_newer(candidate: str, current: str = __version__) -> bool:
    new, cur = parse_version(candidate), parse_version(current)
    if not new:
        return False
    width = max(len(new), len(cur))
    return new + (0,) * (width - len(new)) > cur + (0,) * (width - len(cur))


@dataclass
class Release:
    tag: str
    name: str = ""
    notes: str = ""
    url: str = ""                                          # release page
    assets: list[dict] = field(default_factory=list)       # {"name", "url", "size", "sha256"}

    @property
    def version(self) -> str:
        return self.tag.lstrip("vV")

    def asset(self, pattern: str) -> dict | None:
        """First asset whose name matches `pattern` (regular expression, case ignored)."""
        if not pattern:
            return None
        return next((a for a in self.assets if re.search(pattern, a["name"], re.IGNORECASE)), None)


def _timeout() -> float:
    try:
        return float(os.environ.get("SNAPPY_UPDATE_TIMEOUT", DEFAULT_TIMEOUT))
    except ValueError:
        return DEFAULT_TIMEOUT


def _request(url: str, timeout: float):
    req = urllib.request.Request(url, headers={"Accept": "application/vnd.github+json",
                                               "User-Agent": f"{APP_NAME}/{__version__}"})
    return urllib.request.urlopen(req, timeout=timeout)


def latest_release(timeout: float | None = None) -> Release | None:
    """Latest published release (drafts and pre-releases are left out by GitHub). None when the
    repository has none yet. Raises OSError when GitHub cannot be reached."""
    api = os.environ.get("SNAPPY_UPDATE_API", API).rstrip("/")
    repo = os.environ.get("SNAPPY_UPDATE_REPO", REPO)
    try:
        with _request(f"{api}/repos/{repo}/releases/latest", timeout or _timeout()) as resp:
            data = json.load(resp)
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            return None
        raise
    except ValueError as exc:
        raise OSError(f"unreadable reply from GitHub: {exc}") from exc
    assets = []
    for a in data.get("assets") or []:
        digest = a.get("digest") or ""
        assets.append({"name": a.get("name", ""), "url": a.get("browser_download_url", ""),
                       "size": int(a.get("size") or 0),
                       "sha256": digest[7:].lower() if digest.startswith("sha256:") else ""})
    return Release(tag=data.get("tag_name", ""), name=data.get("name") or "", notes=data.get("body") or "",
                   url=data.get("html_url") or "", assets=assets)


# --------------------------------------------------------------------------
# Log
# --------------------------------------------------------------------------
def cache_root() -> Path:
    """Snappy's local folder (the parent of its caches: %LOCALAPPDATA%\\Snappy, ~/.cache/Snappy)."""
    return system.cache_dir(APP_NAME).parent


def log(message: str) -> None:
    try:
        path = cache_root() / "update.log"
        path.parent.mkdir(parents=True, exist_ok=True)
        if path.exists() and path.stat().st_size > 512_000:
            path.replace(path.with_suffix(".log.1"))
        with open(path, "a", encoding="utf-8") as f:
            f.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')} {message}\n")
    except OSError:
        pass


def disabled() -> bool:
    return bool(os.environ.get("SNAPPY_NO_UPDATE"))


# --------------------------------------------------------------------------
# Installation from the sources (git clone or release archive): run by the launcher, before Snappy
# --------------------------------------------------------------------------
def _git(root: Path, *args: str, timeout: float | None = None) -> subprocess.CompletedProcess:
    env = dict(os.environ, GIT_TERMINAL_PROMPT="0")
    return subprocess.run(["git", *args], cwd=root, capture_output=True, text=True, timeout=timeout,
                          env=env, creationflags=system.NO_WINDOW)


def _running_from(root: Path) -> bool:
    """A Snappy started from this folder (`-m snappy`) is running: its code must not change under it."""
    try:
        import psutil
    except ImportError:
        return False
    own = os.getpid()
    for proc in psutil.process_iter(["pid", "cmdline"]):
        try:
            cmd = proc.info["cmdline"] or []
            if proc.info["pid"] == own or "-m" not in cmd or cmd[cmd.index("-m") + 1:][:1] != ["snappy"]:
                continue
            if Path(proc.cwd()).resolve() == root.resolve():
                return True
        except (psutil.Error, OSError, ValueError):
            continue
    return False


def _venv_python(root: Path) -> Path | None:
    for p in (root / ".venv" / "Scripts" / "python.exe", root / ".venv" / "bin" / "python"):
        if p.exists():
            return p
    return None


def _done(result: str, detail: str = "") -> str:
    if detail:
        log(detail)
    return result


def _pip_install(root: Path) -> subprocess.CompletedProcess | None:
    """Installs requirements.txt in the venv of the sources (None without a venv)."""
    python = _venv_python(root)
    if python is None:
        return None
    return subprocess.run([str(python), "-m", "pip", "install", "--quiet", "-r", "requirements.txt"],
                          cwd=root, capture_output=True, text=True, creationflags=system.NO_WINDOW)


def update_source(root: Path = SOURCE_ROOT, release: Release | None = None) -> str:
    """Moves sources to the latest release: a git clone to its tag, sources installed from a
    release archive to the new archive. Returns what happened (also logged): "updated",
    "up to date", or why nothing changed."""
    if disabled():
        return "disabled"
    is_clone = (root / ".git").exists() and bool(shutil.which("git"))
    from_archive = not (root / ".git").exists() and bool(system.SOURCE_ASSET) \
        and (root / "snappy" / "__init__.py").is_file()
    if not (is_clone or from_archive):
        return "not a git clone"
    if _running_from(root):
        return "running"
    if is_clone and _git(root, "status", "--porcelain", "--untracked-files=no").stdout.strip():
        return _done("local changes", "local changes not committed: not updated")
    if release is None:
        try:
            release = latest_release()
        except OSError as exc:
            return _done("offline", f"could not reach GitHub ({exc}): not updated")
    if release is None or not release.tag:
        return _done("no release", "no release published yet: not updated")
    try:
        return _update_clone(root, release) if is_clone else _update_archive(root, release)
    except (OSError, subprocess.SubprocessError, tarfile.TarError) as exc:
        return _done("failed", f"update failed: {exc}")


def _update_clone(root: Path, release: Release) -> str:
    """Git clone: fast-forward to the release tag, or local commits replayed on top of it."""
    tag = release.tag
    ref = f"refs/tags/{tag}"
    fetch = _git(root, "fetch", "--quiet", "--no-tags", "origin", f"+{ref}:{ref}", timeout=_timeout() * 4)
    if fetch.returncode:
        return _done("offline", f"could not fetch {tag}: {fetch.stderr.strip()}")
    before = _git(root, "rev-parse", "HEAD").stdout.strip()
    if _git(root, "merge-base", "--is-ancestor", ref, "HEAD").returncode == 0:
        return "up to date"                       # this release, or newer commits of your own
    if _git(root, "merge-base", "--is-ancestor", "HEAD", ref).returncode == 0:
        r = _git(root, "merge", "--ff-only", "--quiet", ref)
        if r.returncode:
            return _done("failed", f"fast-forward to {tag} failed: {r.stderr.strip()}")
    else:
        # Local commits on top of an older version: replayed on top of the release.
        r = _git(root, "rebase", "--quiet", ref)
        if r.returncode:
            _git(root, "rebase", "--abort")
            return _done("conflict", f"your local commits conflict with {tag}: kept {before[:7]}")
    after = _git(root, "rev-parse", "HEAD").stdout.strip()
    # New Python dependencies: installed, otherwise back to the previous version.
    if _git(root, "diff", "--quiet", before, after, "--", "requirements.txt").returncode:
        pip = _pip_install(root)
        if pip is not None and pip.returncode:
            _git(root, "reset", "--hard", "--quiet", before)
            return _done("dependencies", f"dependencies of {tag} could not be installed: back to {before[:7]}\n"
                                         + pip.stderr[-2000:])
    return _done("updated", f"updated {before[:7]} -> {tag} ({after[:7]})")


def installed_version(root: Path) -> str:
    """Version of the sources in `root` (snappy/__init__.py)."""
    m = re.search(r'^__version__ = "([^"]+)"', (root / "snappy" / "__init__.py").read_text(encoding="utf-8"), re.M)
    return m.group(1) if m else ""


def _remove(path: Path) -> None:
    if path.is_dir() and not path.is_symlink():
        shutil.rmtree(path)
    elif path.exists() or path.is_symlink():
        path.unlink()


def _update_archive(root: Path, release: Release) -> str:
    """Sources installed from a release archive: each top-level entry of the new archive replaces
    the old one (the snappy package as a whole, so removed modules go too); what the archive does
    not hold stays (.venv, ffmpeg, files of your own). The old entries wait in a backup folder and
    are put back if anything fails."""
    current = installed_version(root)
    if not is_newer(release.version, current):
        return "up to date"
    asset = release.asset(system.SOURCE_ASSET)
    if asset is None:
        return _done("no archive", f"release {release.tag} has no source archive for this system: not updated")
    work = cache_root() / "source-update"
    if work.exists():
        shutil.rmtree(work, ignore_errors=True)
    work.mkdir(parents=True)
    try:
        archive = _download(asset, work)
        unpacked = work / "new"
        with tarfile.open(archive) as tar:
            if hasattr(tarfile, "data_filter"):
                tar.extractall(unpacked, filter="data")       # no paths outside the folder, no devices
            else:
                for m in tar.getmembers():
                    if m.name.startswith(("/", "..")) or "/../" in m.name or not (m.isfile() or m.isdir()):
                        raise tarfile.TarError(f"unexpected entry in the archive: {m.name}")
                tar.extractall(unpacked)
        tops = [p for p in unpacked.iterdir()]
        new = tops[0] if len(tops) == 1 and tops[0].is_dir() else unpacked
        if not (new / "snappy" / "__init__.py").is_file():
            return _done("failed", f"{asset['name']} does not hold Snappy's sources: not updated")
        backup = root / ".update-backup"
        if backup.exists():
            shutil.rmtree(backup)
        backup.mkdir()
        moved, placed = [], []
        try:
            for item in sorted(new.iterdir()):
                target = root / item.name
                if target.exists() or target.is_symlink():
                    target.rename(backup / item.name)
                    moved.append(item.name)
                shutil.move(str(item), str(target))
                placed.append(item.name)
            old_req, new_req = backup / "requirements.txt", root / "requirements.txt"
            if not (old_req.exists() and new_req.exists() and old_req.read_bytes() == new_req.read_bytes()):
                pip = _pip_install(root)
                if pip is not None and pip.returncode:
                    raise _DependencyError(pip.stderr[-2000:])
        except Exception as exc:
            for name in placed:
                _remove(root / name)
            for name in moved:
                (backup / name).rename(root / name)
            shutil.rmtree(backup, ignore_errors=True)
            if isinstance(exc, _DependencyError):
                return _done("dependencies", f"dependencies of {release.tag} could not be installed: "
                                             f"back to {current}\n{exc}")
            raise
        shutil.rmtree(backup, ignore_errors=True)
        return _done("updated", f"updated {current} -> {release.tag} (archive {asset['name']})")
    finally:
        shutil.rmtree(work, ignore_errors=True)


class _DependencyError(Exception):
    pass


# --------------------------------------------------------------------------
# Packaged Snappy (Snappy.exe): download in the background, install when Snappy restarts
# --------------------------------------------------------------------------
def self_updating() -> bool:
    """A packaged Snappy (PyInstaller) whose system installs its own updates."""
    return bool(getattr(sys, "frozen", False)) and system.SELF_UPDATE and not disabled()


def install_dir() -> Path:
    return Path(sys.executable).resolve().parent


def staging_dir() -> Path:
    return cache_root() / "update"


def staged_update() -> tuple[str, Path] | None:
    """(version, unpacked folder) of a downloaded update newer than this Snappy, if any."""
    try:
        info = json.loads((staging_dir() / "ready.json").read_text(encoding="utf-8"))
        folder = Path(info["folder"])
        if is_newer(info["version"]) and (folder / EXE_NAME).is_file():
            return info["version"], folder
    except (OSError, ValueError, KeyError, TypeError):
        pass
    return None


def can_replace(folder: Path) -> bool:
    """The install folder can be swapped (renamed in its parent): not under Program Files without
    administrator rights."""
    probe = folder.parent / f".snappy-update-{os.getpid()}"
    try:
        probe.mkdir()
        probe.rmdir()
        return os.access(folder, os.W_OK)
    except OSError:
        return False


def _download(asset: dict, folder: Path, progress=None) -> Path:
    """Downloads a release asset into `folder`, checking its size and SHA-256 (when GitHub gives
    it). Raises OSError."""
    archive = folder / asset["name"]
    digest = hashlib.sha256()
    with _request(asset["url"], 60) as resp, open(archive, "wb") as out:
        total = asset["size"] or int(resp.headers.get("Content-Length") or 0)
        got = 0
        while chunk := resp.read(1024 * 1024):
            out.write(chunk)
            digest.update(chunk)
            got += len(chunk)
            if progress and total:
                progress(got / total)
    if asset["size"] and archive.stat().st_size != asset["size"]:
        raise OSError(f"incomplete download ({archive.stat().st_size} / {asset['size']} bytes)")
    if asset["sha256"] and digest.hexdigest() != asset["sha256"]:
        archive.unlink(missing_ok=True)
        raise OSError("checksum mismatch: download rejected")
    return archive


def download_release(release: Release, progress=None, pattern: str | None = None) -> Path:
    """Downloads and unpacks the zip of a release for this system (`pattern`: system.UPDATE_ASSET);
    returns the folder holding the executable. Raises OSError (network, checksum, archive)."""
    pattern = system.UPDATE_ASSET if pattern is None else pattern
    asset = release.asset(pattern)
    if asset is None:
        raise OSError(f"release {release.tag} has no archive for this system")
    root = staging_dir()
    if root.exists():
        shutil.rmtree(system.long_path(root), ignore_errors=True)
    root.mkdir(parents=True, exist_ok=True)
    archive = _download(asset, root, progress)
    unpacked = root / "new"
    try:
        with zipfile.ZipFile(archive) as z:
            z.extractall(system.long_path(unpacked))
    except zipfile.BadZipFile as exc:
        raise OSError(f"damaged archive: {exc}") from exc
    finally:
        archive.unlink(missing_ok=True)
    # The zip holds the executable at its root or in a single folder.
    exes = [p for p in sorted(unpacked.glob(EXE_NAME)) + sorted(unpacked.glob(f"*/{EXE_NAME}")) if p.is_file()]
    if not exes:
        raise OSError(f"{EXE_NAME} is not in the archive")
    folder = exes[0].parent
    (root / "ready.json").write_text(json.dumps({"version": release.version, "folder": str(folder)}),
                                     encoding="utf-8")
    log(f"{release.tag} downloaded: installed when Snappy restarts")
    return folder


def apply_staged(wait_pid: int, args: list[str] | None = None, target: Path | None = None) -> bool:
    """Has the system install the downloaded update once process `wait_pid` has quit, then start
    Snappy again with `args`. The caller quits right after. False: nothing to install."""
    staged = staged_update()
    if staged is None:
        return False
    version, folder = staged
    if not system.install_update(folder, target or install_dir(), staging_dir(), cache_root() / "update.log",
                                 version, wait_pid, args or []):
        return False
    log(f"installing {version} after Snappy quits")
    return True


def main(argv: list[str] | None = None) -> int:
    """Launcher of an installation from the sources: `python -m snappy.update` before Snappy."""
    result = update_source()
    if result not in ("up to date", "not a git clone", "disabled", "running"):
        print(f"Snappy update: {result}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
