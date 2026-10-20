"""Edit of a clip: excerpt (start / end), removed passages, screen switches, track mix, export
presets and undo history. Only data and pure functions.

An edit is stored in the library (`Clip.edit`, `Clip.angles`):
  {"range": [start, end], "cuts": [[a, b], ...], "mix": [{"gain", "muted", "points"}, ...],
   "preset": "discord" | "hq" | "light" | "copy" | "custom"}
"""

from __future__ import annotations

import json

from ..i18n import tr

# Export presets: (key, name, sentence shown at the top of the summary).
PRESETS = [
    ("discord", "Discord", tr("To send it on Discord")),
    ("hq", "HQ", tr("High quality: original resolution and frame rate, barely compressed")),
    ("light", tr("Compact"), tr("Small file, quick to send anywhere")),
    ("copy", tr("Original"), tr("Original quality, instant: nothing is re-encoded")),
]
HEIGHTS = [0, 2160, 1440, 1080, 720, 480]   # 0 = original
FPS_CHOICES = [0, 120, 60, 30]               # 0 = original
UNDO_DEPTH = 100


# ---------------------------------------------------------------- excerpt and removed passages
def merge_ranges(ranges: list[tuple[float, float]], min_len: float = 0.05) -> list[tuple[float, float]]:
    out: list[list[float]] = []
    for a, b in sorted((min(a, b), max(a, b)) for a, b in ranges):
        if b - a < min_len:
            continue
        if out and a <= out[-1][1] + 1e-6:
            out[-1][1] = max(out[-1][1], b)
        else:
            out.append([a, b])
    return [(a, b) for a, b in out]


def kept_segments(t_in: float, t_out: float, cuts: list[tuple[float, float]]) -> list[tuple[float, float]]:
    """Passages kept between the start and the end, once the removed passages are taken out."""
    segs, pos = [], t_in
    for a, b in merge_ranges(cuts):
        a, b = max(a, t_in), min(b, t_out)
        if b <= a:
            continue
        if a - pos > 1e-3:
            segs.append((pos, a))
        pos = max(pos, b)
    if t_out - pos > 1e-3:
        segs.append((pos, t_out))
    return segs or [(t_in, t_out)]


# ---------------------------------------------------------------- multi-screen editing
def angle_at(angles: list, t: float) -> int:
    """Screen shown at time t according to the switches [[time, screen], ...] (sorted)."""
    k = 0
    for at, screen in sorted(angles or [[0.0, 0]]):
        if at <= t + 1e-6:
            k = int(screen)
        else:
            break
    return k


def video_plan(segments: list[tuple[float, float]], angles: list, only: int | None = None) -> list[tuple[float, float, int]]:
    """Pieces (start, end, screen) in clip time, for the kept passages.
    only=None: multicam edit from the switches; only=k: everything on screen k."""
    switches = [[0.0, only]] if only is not None else sorted(angles or [[0.0, 0]])
    plan: list[tuple[float, float, int]] = []
    for a, b in segments:
        cuts = [a] + [t for t, _ in switches if a < t < b] + [b]
        first = True
        for s, e in zip(cuts, cuts[1:]):
            if e - s < 1e-3:
                continue
            k = angle_at(switches, s)
            if not first and plan and plan[-1][2] == k and abs(plan[-1][1] - s) < 1e-6:
                plan[-1] = (plan[-1][0], e, k)
            else:
                plan.append((s, e, k))
            first = False
    return plan


def display_order(screens: list, order: list[int]) -> list[int]:
    """Video streams of a clip ([{monitor, ...}]) in the chosen display order (screen numbers); a
    screen missing from that order stays after the others, in file order."""
    rank = {m: i for i, m in enumerate(order)}
    return sorted(range(len(screens)), key=lambda k: (rank.get(screens[k].get("monitor", k), len(rank)), k))


def screen_name(screens: list, k: int) -> str:
    """"Screen N": the number of the screen in the system (as on the timeline)."""
    m = screens[k].get("monitor") if k < len(screens) else None
    return tr("Screen {n}", n=(m if isinstance(m, int) else k) + 1)


# ---------------------------------------------------------------- export
def discord_settings(duration: float, limit_mb: float, src_height: int, src_fps: int) -> tuple[int, int]:
    """Resolution and frame rate that fit within Discord's limit (0 = keep the original)."""
    bps = limit_mb * 1e6 * 8 * 0.94 / max(duration, 0.1) - 160_000
    h = 1440 if bps >= 9e6 else 1080 if bps >= 5e6 else 720 if bps >= 2e6 else 480
    fps = 60 if bps >= 1.5e6 else 30
    return (h if h < src_height else 0), (fps if fps < src_fps else 0)


def export_tag(settings: dict, height: int, fps: int, n_screens: int) -> str:
    """Suffix of the exported file name ("720p60-10mb-screen2"...)."""
    if settings["copy"]:
        return tr("excerpt")
    tag = f"{settings['height'] or height}p{settings['fps'] or fps}"
    tag += "-" + tr("{n}mb", n=settings["target"]) if settings["target"] else f"-q{settings['qp']}"
    if settings["codec"] != "h264":
        tag += f"-{settings['codec']}"
    if n_screens > 1:
        tag += "-" + (tr("multicam") if settings["screen"] is None else tr("screen{n}", n=settings["screen"] + 1))
    return tag


# ---------------------------------------------------------------- track mix
def track_mix(edit: dict | None, kinds: list[str], levels: dict[str, float]) -> list[dict]:
    """Setting of each separate track {gain, muted, points}: the one stored in the edit, otherwise
    the default level of its kind. A fixed volume with a curve is folded into the curve: a single
    line to adjust."""
    mix = (edit or {}).get("mix") or []
    out = []
    for k, kind in enumerate(kinds):
        m = mix[k] if k < len(mix) else {}
        gain = m.get("gain", levels.get(kind, 1.0))
        pts = [tuple(p) for p in m.get("points", [])]
        if pts and gain != 1.0:
            pts, gain = [(pt, round(v * gain, 3)) for pt, v in pts], 1.0
        out.append({"gain": gain, "muted": m.get("muted", False), "points": pts})
    return out


# ---------------------------------------------------------------- signatures and history
def snapshot(t_in: float, t_out: float, cuts: list, angles: list, mix: list[dict]) -> str:
    """State of the edit for the undo history."""
    return json.dumps({"range": [round(t_in, 3), round(t_out, 3)],
                       "cuts": [[round(a, 3), round(b, 3)] for a, b in cuts],
                       "angles": angles, "mix": mix}, sort_keys=True)


def signature(t_in: float, t_out: float, cuts: list, mix: list[dict], angles: list | None) -> str:
    """Signature of the edit (excerpt, cuts, mix, switches): compared with the last export's."""
    return json.dumps({"range": [round(t_in, 2), round(t_out, 2)],
                       "cuts": [[round(a, 2), round(b, 2)] for a, b in cuts],
                       "mix": [[round(m["gain"], 3), m["muted"], [[round(t, 2), round(v, 3)] for t, v in m["points"]]]
                               for m in mix],
                       "angles": angles}, sort_keys=True)


class EditHistory:
    """Undo / redo: stack of edit states (`snapshot`)."""

    def __init__(self):
        self.undo_stack: list[str] = []
        self.redo_stack: list[str] = []
        self.current: str | None = None

    def reset(self, state: str) -> None:
        self.undo_stack.clear()
        self.redo_stack.clear()
        self.current = state

    def commit(self, state: str) -> bool:
        """New committed state; False if it is the same as the previous one."""
        if state == self.current:
            return False
        if self.current is not None:
            self.undo_stack.append(self.current)
            del self.undo_stack[:-UNDO_DEPTH]
        self.current = state
        self.redo_stack.clear()
        return True

    def undo(self, state_now: str) -> str | None:
        if not self.undo_stack:
            return None
        self.redo_stack.append(state_now)
        self.current = self.undo_stack.pop()
        return self.current

    def redo(self, state_now: str) -> str | None:
        if not self.redo_stack:
            return None
        self.undo_stack.append(state_now)
        self.current = self.redo_stack.pop()
        return self.current

    @property
    def can_undo(self) -> bool:
        return bool(self.undo_stack)

    @property
    def can_redo(self) -> bool:
        return bool(self.redo_stack)
