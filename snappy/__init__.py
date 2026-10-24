"""Snappy: instant replay of the last moments of your screen.

Layout (MVC):
  models/       data: settings, clip library, edits, games
  services/     engine without UI: replay buffer, audio capture, FFmpeg, export, sounds
  views/        Qt interface: shows the models and emits signals
  controllers/  connect the views to the models and services
  platforms/    everything that depends on the operating system (Windows, Linux)
  i18n.py       interface language (English, French translation in locales/)
"""

from pathlib import Path

__version__ = "0.2.3"
APP_NAME = "Snappy"
# Folder that contains the package (sources): bundled FFmpeg, launch at login.
SOURCE_ROOT = Path(__file__).resolve().parent.parent
