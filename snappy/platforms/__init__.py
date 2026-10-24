"""System layer: everything that depends on the operating system.

The rest of Snappy never imports `platforms.windows` directly: it goes through `system`, the
implementation for the current system: `windows/`, `linux/`, or `generic` (the neutral fallback
for other systems). Porting Snappy to another OS (macOS...) means writing a module that offers
the same functions as `generic`, then selecting it below.

What `system` provides:
  NAME, CAN_CAPTURE                     system name; screen and audio capture available
  data_dir(app), cache_dir(app),        application folders
  buffer_dir(app), videos_dir()
  enable_dpi_awareness(), set_app_id()  process setup before Qt
  foreground_app_info()                 (readable name, executable) of the foreground app
  reveal(path), open_folder(path)       file manager
  send_to_recycle_bin(paths)            system trash
  set_autostart(enabled, minimized)     start at login
  accent_color()                        system accent color
  steam_value(name), steam_dirs()       Steam location, running game
  play_wav(path)                        confirmation sound
  NO_WINDOW, BACKGROUND,                subprocess options (FFmpeg);
  kill_with_parent(proc / pid),         a subprocess dies with Snappy;
  boost_priority(), EXE_SUFFIX          audio process priority; ".exe"
  GlobalHotkeys, parse_hotkey           global keyboard shortcuts
  screen_capture_methods(), ...         FFmpeg screen capture arguments
  audio_pipe_name(), PipeWriter,        audio stream to FFmpeg; capture sources per
  make_capture_source(), capture_plan(), application or microphone; apps playing sound
  list_microphones(), audio_apps(),
  prepare_audio_process(), set_app_pid()
  replay_buffer_class()                 replay engine (Windows: FFmpeg; Linux: gpu-screen-recorder)
  ENCODER_VENDORS, ENCODER_CHOICES,     graphics card encoders (exports, buffer);
  render_node()                         VA-API render node
  TEXT, CHAT_APPS, OVERLAY_TOAST,       interface texts, suggested chat apps,
  system_sounds()                       notification placed by Snappy; system sounds
  show_overlay_toast(...)               toast over the game where OVERLAY_TOAST is False (Wayland)
  MANUAL_RECORDING                      manual recording (start / stop) next to the replay
  SELF_UPDATE, UPDATE_ASSET,            packaged Snappy that updates itself from a release asset
  install_update(...), long_path(path)  (Windows: Snappy.exe); swapping the install folder
  SOURCE_ASSET                          release archive replacing sources not installed by git (Linux)
"""

import sys

if sys.platform == "win32":
    from . import windows as system
elif sys.platform.startswith("linux"):
    from . import linux as system
else:
    from . import generic as system

__all__ = ["system"]
