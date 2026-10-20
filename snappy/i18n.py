"""User interface language.

Interface strings are written in English in the code and wrapped in `tr()`. When the system
language has a catalog in `snappy/locales/` (French: `fr.py`), `tr()` returns the translation;
otherwise it returns the English text. Set `SNAPPY_LANG=en` or `SNAPPY_LANG=fr` to override the
system language.

Placeholders use `str.format` names, so translations can reorder them:
    tr("{n} clips saved", n=3)
"""

from __future__ import annotations

import importlib
import os

LANGUAGES = ("en", "fr")

_language: str | None = None
_catalog: dict[str, str] | None = None


def _system_language() -> str:
    try:
        from PySide6.QtCore import QLocale

        for name in QLocale.system().uiLanguages():
            return name[:2].lower()
    except Exception:
        pass
    for var in ("LC_ALL", "LC_MESSAGES", "LANG"):
        if os.environ.get(var):
            return os.environ[var][:2].lower()
    return "en"


def language() -> str:
    """Language of the interface: "en" or "fr"."""
    global _language
    if _language is None:
        wanted = os.environ.get("SNAPPY_LANG", "").strip().lower()[:2] or _system_language()
        _language = wanted if wanted in LANGUAGES else "en"
    return _language


def catalog() -> dict[str, str]:
    """English text -> translated text for the current language (empty for English)."""
    global _catalog
    if _catalog is None:
        lang = language()
        _catalog = {} if lang == "en" else importlib.import_module(f"snappy.locales.{lang}").STRINGS
    return _catalog


def tr(text: str, /, **values) -> str:
    """Translated interface text; `values` fill the {placeholders}."""
    text = catalog().get(text, text)
    return text.format(**values) if values else text


def set_language(lang: str | None) -> None:
    """Forces a language (tests); None goes back to the system language."""
    global _language, _catalog
    _language = lang if lang in LANGUAGES else None
    _catalog = None
