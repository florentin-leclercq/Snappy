"""Interface translations: every tr() string has a French translation with the same placeholders,
and the code itself is written in English (no French text outside the translation catalog).
Run: python -m unittest discover tests"""

import ast
import os
import re
import string
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.environ.setdefault("SNAPPY_LANG", "en")

from snappy import i18n  # noqa: E402
from snappy.locales import fr  # noqa: E402

# Characters that only show up in French text.
FRENCH_CHARS = re.compile(r"[éèêëàâîïôûùüçœÉÈÊÀÂÎÔÛÇŒ«»]")
CATALOG = ROOT / "snappy" / "locales" / "fr.py"


def source_files():
    for folder in ("snappy", "tests", "scripts"):
        yield from sorted((ROOT / folder).rglob("*.py"))


def tr_strings(path: Path) -> list[tuple[int, str]]:
    """Literal first arguments of tr() calls in a file."""
    out = []
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if isinstance(node, ast.Call) and getattr(node.func, "id", getattr(node.func, "attr", None)) == "tr" \
                and node.args and isinstance(node.args[0], ast.Constant) and isinstance(node.args[0].value, str):
            out.append((node.lineno, node.args[0].value))
    return out


def placeholders(text: str) -> set[str]:
    return {name for _lit, name, _spec, _conv in string.Formatter().parse(text) if name is not None}


class TestTranslations(unittest.TestCase):
    def test_every_string_is_translated(self):
        missing = [f"{p.relative_to(ROOT)}:{line} {text!r}" for p in source_files()
                   for line, text in tr_strings(p) if text not in fr.STRINGS]
        self.assertEqual(missing, [], "tr() strings without a French translation")

    def test_placeholders_match(self):
        wrong = [k for k, v in fr.STRINGS.items() if placeholders(k) != placeholders(v)]
        self.assertEqual(wrong, [], "translations whose {placeholders} differ from the English text")

    def test_language_switch(self):
        try:
            i18n.set_language("fr")
            self.assertEqual(i18n.tr("Settings"), fr.STRINGS["Settings"])
            i18n.set_language("en")
            self.assertEqual(i18n.tr("Settings"), "Settings")
            self.assertEqual(i18n.tr("{n} clips", n=3), "3 clips")
        finally:
            i18n.set_language(None)


class TestEnglishSource(unittest.TestCase):
    def test_no_french_outside_the_catalog(self):
        found = []
        for path in list(source_files()) + [ROOT / "README.md"] + sorted(ROOT.glob("*.sh")) \
                + sorted(ROOT.glob("*.ps1")) + sorted((ROOT / "scripts").glob("*.ps1")) + [ROOT / "run.bat"]:
            if path == CATALOG or path == Path(__file__).resolve():
                continue
            for n, line in enumerate(path.read_text(encoding="utf-8-sig").splitlines(), 1):
                if FRENCH_CHARS.search(line):
                    found.append(f"{path.relative_to(ROOT)}:{n}: {line.strip()[:90]}")
        self.assertEqual(found, [], "French text left in the code or the docs")


if __name__ == "__main__":
    unittest.main()
