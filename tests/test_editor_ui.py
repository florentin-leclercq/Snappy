"""Editor interactions driven with simulated mouse / keys (no display needed: offscreen Qt):
moving a removed passage on the filmstrip, B to switch screens at the playhead.
Run: python -m unittest discover tests"""

import os
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("SNAPPY_LANG", "en")

from PySide6.QtCore import QPoint, Qt  # noqa: E402
from PySide6.QtTest import QTest  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from snappy.views.timeline import EditorTimeline  # noqa: E402

app = QApplication.instance() or QApplication([])


class TestMoveCut(unittest.TestCase):
    def setUp(self):
        self.tl = EditorTimeline()
        self.tl.resize(1200, 400)
        self.tl.set_clip(10.0, ["Screen 1"], [], [0])
        self.tl.show()
        app.processEvents()
        self.tl.cuts = [[2.0, 3.0], [5.0, 6.0]]
        self.changed, self.seeks = [], []
        self.tl.cutsChanged.connect(lambda c: self.changed.append([list(x) for x in c]))
        self.tl.seekRequested.connect(self.seeks.append)
        rows = [r for r in self.tl._rows() if r[0] == "strip"]
        self.y = int(rows[0][2] + rows[0][3] / 2)

    def drag(self, t0: float, t1: float) -> None:
        x0, x1 = int(self.tl.t_to_x(t0)), int(self.tl.t_to_x(t1))
        QTest.mousePress(self.tl, Qt.LeftButton, Qt.NoModifier, QPoint(x0, self.y))
        for x in range(x0, x1, 4 if x1 > x0 else -4):
            QTest.mouseMove(self.tl, QPoint(x, self.y))
        QTest.mouseMove(self.tl, QPoint(x1, self.y))
        QTest.mouseRelease(self.tl, Qt.LeftButton, Qt.NoModifier, QPoint(x1, self.y))

    def test_inside_is_a_move(self):
        self.assertEqual(self.tl._hit(self.tl.t_to_x(2.5), self.y)["kind"], "cut_move")
        self.assertEqual(self.tl._hit(self.tl.t_to_x(2.0), self.y)["kind"], "cut_a")   # edges still resize

    def test_move_keeps_length(self):
        self.drag(2.5, 3.5)
        (a, b), second = self.tl.cuts
        self.assertAlmostEqual(a, 3.0, delta=0.03)
        self.assertAlmostEqual(b - a, 1.0, delta=0.001)
        self.assertEqual(second, [5.0, 6.0])
        self.assertTrue(self.changed)

    def test_move_stops_at_neighbours_and_clip_ends(self):
        self.drag(5.5, 9.9)                        # past the end of the clip: stops at the end
        self.assertEqual(self.tl.cuts[-1], [9.0, 10.0])
        self.drag(2.5, 0.0)                        # past the start: stops at the start
        self.assertEqual(self.tl.cuts[0], [0.0, 1.0])
        self.drag(0.5, 9.5)                        # into the next passage: stops against it, then the
        self.assertEqual(self.tl.cuts, [[8.0, 10.0]])   # two touching passages make one

    def test_click_inside_seeks(self):
        x = int(self.tl.t_to_x(2.5))
        QTest.mouseClick(self.tl, Qt.LeftButton, Qt.NoModifier, QPoint(x, self.y))
        self.assertEqual(self.tl.cuts, [[2.0, 3.0], [5.0, 6.0]])
        self.assertTrue(self.seeks and abs(self.seeks[-1] - 2.5) < 0.03)


class TestSwitchNext(unittest.TestCase):
    """B in the editor: next screen at the playhead, or the one forced in the preview."""

    def page(self, n_screens: int, order: list[int], pos: float, preview=None):
        from types import SimpleNamespace

        from snappy.views.editor import EditorPage

        tl = EditorTimeline()
        tl.set_clip(10.0, [f"Screen {k + 1}" for k in range(n_screens)], [], order)
        commits = []
        fake = SimpleNamespace(clip=SimpleNamespace(n_screens=n_screens), screen_order=order, preview_screen=preview,
                               timeline=tl, player=SimpleNamespace(position=lambda: pos * 1000),
                               _commit=lambda: commits.append(1))
        EditorPage.switch_next(_Proxy(fake))
        return tl.angles, commits


class _Proxy:
    """Gives switch_next the attributes of the fake page, `angles` coming from its timeline."""

    def __init__(self, ns):
        self.__dict__["ns"] = ns

    def __getattr__(self, name):
        if name == "angles":
            return self.ns.timeline.angles
        return getattr(self.ns, name)


class TestSwitchNextCases(TestSwitchNext):
    def test_next_screen(self):
        angles, commits = self.page(2, [0, 1], 4.0)
        self.assertEqual(angles, [[0.0, 0], [4.0, 1]])
        self.assertEqual(commits, [1])

    def test_wraps_and_follows_display_order(self):
        angles, _ = self.page(3, [2, 0, 1], 0.0)     # screen 0 shown, next in the display order: 1
        self.assertEqual(angles, [[0.0, 1]])

    def test_forced_preview_screen_wins(self):
        angles, _ = self.page(3, [0, 1, 2], 3.0, preview=2)
        self.assertEqual(angles, [[0.0, 0], [3.0, 2]])

    def test_single_screen_does_nothing(self):
        angles, commits = self.page(1, [0], 3.0)
        self.assertEqual((angles, commits), ([[0.0, 0]], []))


if __name__ == "__main__":
    unittest.main()
