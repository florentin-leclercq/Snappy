"""Sets Snappy's version in snappy/__init__.py and pyproject.toml (the release workflow checks that
the pushed tag matches it).

    python scripts/set_version.py            asks for the version (Enter: next patch version)
    python scripts/set_version.py 0.3.0      no question

Launchers: scripts/set_version.sh (Linux), scripts/set_version.ps1 (Windows). Only the two files
change: committing and tagging stay yours (the next steps are printed).
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
VERSION = re.compile(r"^\d+\.\d+\.\d+$")
# (file, line holding the version); the first match only (pyproject: the [project] version).
TARGETS = (("snappy/__init__.py", re.compile(r'^(__version__ = ")([^"]*)(")', re.M)),
           ("pyproject.toml", re.compile(r'^(version = ")([^"]*)(")', re.M)))


def current(root: Path = ROOT) -> str:
    text = (root / TARGETS[0][0]).read_text(encoding="utf-8")
    return TARGETS[0][1].search(text).group(2)


def next_patch(version: str) -> str:
    major, minor, patch = (int(p) for p in version.split("."))
    return f"{major}.{minor}.{patch + 1}"


def as_tuple(version: str) -> tuple[int, ...]:
    return tuple(int(p) for p in version.split("."))


def apply(version: str, root: Path = ROOT) -> list[str]:
    """Writes `version` into both files (line endings kept); returns the files changed."""
    if not VERSION.match(version):
        raise ValueError(f"{version!r} is not a version like 1.2.3")
    changed = []
    for name, pattern in TARGETS:
        path = root / name
        with open(path, encoding="utf-8", newline="") as f:      # newline="": CRLF / LF kept as they are
            text = f.read()
        if not pattern.search(text):
            raise ValueError(f"no version found in {name}")
        new = pattern.sub(lambda m: m.group(1) + version + m.group(3), text, count=1)
        if new != text:
            with open(path, "w", encoding="utf-8", newline="") as f:
                f.write(new)
            changed.append(name)
    return changed


def ask(cur: str) -> str | None:
    suggested = next_patch(cur) if VERSION.match(cur) else ""
    while True:
        try:
            answer = input(f"New version [{suggested}]: ").strip().lstrip("vV") or suggested
        except (EOFError, KeyboardInterrupt):
            print()
            return None
        if not VERSION.match(answer):
            print("Expected a version like 1.2.3 (three numbers).")
            continue
        if VERSION.match(cur) and as_tuple(answer) <= as_tuple(cur):
            try:
                if input(f"{answer} is not newer than {cur}: the updates would not offer it. Use it anyway? [y/N] "
                         ).strip().lower() not in ("y", "yes", "o", "oui"):
                    continue
            except (EOFError, KeyboardInterrupt):
                print()
                return None
        return answer


def main(argv: list[str]) -> int:
    cur = current()
    print(f"Current version: {cur}")
    if argv:
        version = argv[0].strip().lstrip("vV")
    else:
        version = ask(cur)
        if version is None:
            print("Nothing changed.")
            return 1
    try:
        changed = apply(version)
    except ValueError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    if not changed:
        print(f"Already {version}: nothing changed.")
        return 0
    print(f"Version {version} set in: {', '.join(changed)}")
    print("Next steps:")
    print(f'  git commit -am "Version {version}"')
    print(f"  git tag v{version} && git push origin main v{version}    (the Release workflow publishes it)")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
