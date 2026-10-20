# Snappy

Instant replay for Windows and Linux: Snappy keeps recording the last seconds of your screen (video + sound) and saves them as a clip when you press a shortcut.

> [!WARNING]
> **This project was made by an artificial intelligence.** Snappy's code, documentation and tests were written
> entirely (or almost entirely) by an AI (Claude, by Anthropic), from the requests of a person who did not review
> every line.
>
> **No warranty.** The software is provided "as is", without warranty of any kind, express or implied, including
> but not limited to working correctly, security or fitness for a particular purpose. It may contain bugs, lose or
> damage recordings, or misbehave on your machine. You use it at your own risk: the authors cannot be held liable
> for any damage.

The interface is available in English and French: it follows the system language (`SNAPPY_LANG=en` or
`SNAPPY_LANG=fr` forces one of them).

## Features

**Capture**
- **Replay buffer**: length configurable from 5 s to 10 min (30 s by default), clips of a controlled length (within 1 s, never longer).
- **Several screens** recorded at once, in the same file (one video stream per screen, perfectly in sync).
- **Separate audio tracks**, one per source: the game (all the PC sound, everything except the voice chat, or a single application), the voice chat (Discord, TeamSpeak…), the microphone, and optionally one track per application (Spotify, browser…). On Windows, the sound of each application is captured with *process loopback*: nothing is rerouted, your outputs stay untouched, and an application started after Snappy is found by itself. Audio mixers that replay all the sound, microphone included (SteelSeries Sonar, Voicemeeter…), are left out of the game track. The sound Snappy plays itself (editor, gallery hover, confirmation sound) is never recorded into the other tracks; add "Snappy.exe" to the separate applications to keep it on its own track (from run.bat as well as from the .exe). The sound of an application playing into a 7.1 output (Sonar, "surround" headsets) is brought back to its real level: Windows lowers it by about 13 dB when converting it to stereo.
- The file starts with a **"Mix" track** (the tracks mixed at their default levels): Discord, browsers and the file manager, which only play the first track, hear everything.
- **Detection of the running Steam game**: the clip is named after it and filed under the game, with its Steam artwork (nothing is downloaded).
- **GPU encoding** (NVIDIA NVENC, AMD AMF, Intel Quick Sync on Windows; NVENC and VA-API on Linux) in H.264, HEVC or AV1, with automatic fallback to the processor.

**Library**
- Gallery grouped by day, resizable cards. On hover, the clip plays by itself (small silent preview video); hovering the progress bar seeks through the clip. Star and trash on each card.
- Sidebar by game / application (Steam or executable icons), favorites, search, period (today, 7 days, 30 days, a given day), sorting (newest, oldest, longest, largest).
- **Drag and drop**: drag a card (or a selection) into Discord, a folder, a browser…
- Multiple selection (box, `Ctrl`/`Shift` + click): export for Discord, copy, game, favorites, deletion.
- **Undoable trash** ("Undo" for a few seconds, then the system trash).
- Renaming a clip also renames its file (the name people see when it is shared).
- **Accent color** of your choice (Settings › Appearance): a few ready-made colors, an exact color, or the system one. Applied right away.

**Editor**
- Excerpt start / end, **removed passages** (`C` … `C`, `Shift` + drag, right-click to put back), undo / redo.
- **Multi-screen editing**: each screen has its row of previews on the timeline; switch from one screen to another along the clip (click on the row, keys `1`, `2`…), preview of the multicam edit or of a single screen.
- **Mixing on the timeline**: drag the line of a track for its volume (0–200 %, shown on hover), click on it to add points and draw a **volume curve**; M (mute) and S (solo) buttons; the waveform follows the volume that was set (red where it clips). Playback mixes the tracks live.
- **Timeline zoom**: `Ctrl` + wheel or pinch on the touchpad (around the mouse, down to 1 s across the whole width), `Shift` + wheel to move in time, double-click on the ruler to see everything again; `Alt` + wheel for the track height. The wheel alone scrolls the tracks.
- Title editable in place (click on the title or the pencil), favorite star next to it, recognized game just below ("Unknown" otherwise, pencil to change it).
- **Export**: Discord (stays under the limit, 10 MB by default), HQ, Compact and Original (instant, no re-encoding) presets; resolution, frame rate, codec, target size, quality; **normalized volume** (-14 LUFS, without clipping); single screen or multicam edit; the sound alone as MP3 with every export. Shows whether the last export is up to date with the edit.
- **Sharing**: original clip, exported version, clip sound and exported sound (MP3), to drag or copy.
- The edit (excerpt, cuts, mix, switches) is kept for each clip.

**Optimizations**
- NV12 capture pipeline (native format of GPU encoders), a single FFmpeg for all the screens and all the tracks, audio tracks in a single pipe (aligned to the sample) on Windows.
- Forced IDR keyframes for every encoder: each 1 s segment stands on its own, saving re-encodes nothing.
- Exports: encoder picked by a **GPU vs processor benchmark** run once (cached), automatic processor fallback, new attempt if the target size is exceeded.
- Thumbnails, previews and exports at **low priority** (two tasks at a time at most): the game comes first.
- If FFmpeg runs without producing a segment (incompatible encoder), Snappy notices and switches to the software encoder.

## Quick start (development, Windows)

Requirements: Windows 10 (2004+) or 11, Python 3.10+.

```powershell
py -3 -m venv .venv
.\.venv\Scripts\pip install -r requirements.txt
powershell -ExecutionPolicy Bypass -File scripts\get_ffmpeg.ps1   # FFmpeg >= 6 in .\ffmpeg\bin
.\.venv\Scripts\python -m snappy
```

`--minimized` option: start directly in the notification area. `--demo` option: replaces the screen with test patterns and the sound with beeps (useful to develop without a game).

## Linux

Requirements: a Wayland or X11 desktop with PipeWire, [gpu-screen-recorder](https://git.dec05eba.com/gpu-screen-recorder)
(package or Flatpak `com.dec05eba.gpu_screen_recorder`), FFmpeg, Python 3.10+.

```bash
./install.sh        # dependencies (installed right away on Arch and derivatives), .venv, applications menu
snappy              # or ./run.sh from this folder
./uninstall.sh      # removes the menu entry, the login item and the shortcut (clips and settings are kept)
```

On Linux, Snappy hands the capture over to **gpu-screen-recorder**:

- **Video**: direct capture of the screens (KMS on Wayland, X11 otherwise), or the desktop portal (permission asked
  once, a single screen), encoded by the graphics card (NVENC, VA-API), with automatic processor fallback.
  The replay is kept **in memory** by gpu-screen-recorder; one recorder per recorded screen.
- **Sound**: one track per source through PipeWire, without rerouting anything: the game (`default_output`, or
  everything except the chat with `app-inverse:`), the voice chat (`app:vesktop`…), the microphone, the separate
  applications. The names are PipeWire's ("Find applications that play sound" button). The sound Snappy plays is
  named "Snappy": add it to the separate applications to keep it on its own track.
- **Global shortcut**: on Wayland, an application cannot listen to the keyboard, so Snappy installs its shortcut in
  the desktop (KDE: kglobalaccel, GNOME: custom shortcuts), which runs `snappy --hotkey save` and notifies the running
  Snappy. On X11 (i3, XFCE…), Snappy grabs the key itself. Elsewhere (Hyprland, Sway…), the settings show the command
  to bind, for example `bind = ALT, F10, exec, snappy --hotkey save`.
- **Desktop**: start at login (`~/.config/autostart`), freedesktop trash, KDE or GNOME accent color, running Steam game
  (`SteamAppId` variable of the processes), theme sounds (KDE Ocean, freedesktop).
- **Without graphics acceleration** (virtual machine, missing driver), gpu-screen-recorder does not start: on X11,
  Snappy falls back by itself to its FFmpeg engine (`x11grab`, sound read through PulseAudio / PipeWire). In this mode,
  the sound is not split per application: the game track holds the whole audio output. Pick the "FFmpeg X11" method in
  the settings to use it from the start.
- **Confirmation over the game on Wayland**: a small toast in the layer-shell "overlay" layer (KDE, Hyprland,
  Sway…), on the recorded screen, that never takes the focus and lets the clicks through. It needs PyGObject
  and gtk4-layer-shell in the system Python (`pacman -S python-gobject gtk4-layer-shell`); otherwise, and on
  GNOME, the confirmation is a desktop notification.
- **Manual recording** (`Alt+F9` by default, press again to stop): records everything from now on, on top of the
  replay, through gpu-screen-recorder's command socket (no second encode). With several screens, they are aligned
  on the timestamp of their first frame. The recording time shows in the window and the notification area.

## Building the executable (Windows)

```powershell
powershell -ExecutionPolicy Bypass -File build.ps1
```

Produces `dist\Snappy\Snappy.exe`, self-contained, with FFmpeg included.

## Shortcuts

| Where | Key | Action |
|---|---|---|
| Everywhere | `Alt+F10` (configurable) | Save a clip |
| Everywhere (Linux) | `Alt+F9` (configurable) | Start / stop a recording |
| Gallery | `Ctrl`/`Shift` + click, `Ctrl+A`, `Esc` | Multiple selection |
| Editor | `Space` | Play / pause |
| Editor | `I` / `O` | Excerpt start / end |
| Editor | `C` then `C` | Remove a passage |
| Editor | `1`, `2`… | Switch to that screen at the playhead |
| Editor | `←` / `→`, `,` / `.` | ±1 s, previous / next frame |
| Editor | `Ctrl+Z` / `Ctrl+Y` | Undo / redo |
| Editor | `L`, `M`, `F11` | Loop, mute playback, full screen |
| Editor | `Ctrl+E` | Export |
| Editor | `F`, `F2`, `Del`, `Esc` | Favorite, rename, trash, back |
| Editor | `Ctrl` + wheel, `Shift` + wheel, `Alt` + wheel | Timeline zoom, move in time, track height |

## How it works (Windows)

```
Screen 1, screen 2… (Desktop Duplication) ─┐
                                           ├─► one FFmpeg ─► 1 s MPEG-TS segments in a ring
Game / chat / mic / apps (WASAPI) ─────────┘    ▲            (%LOCALAPPDATA%\Snappy\buffer)
  one track per source, side by side            │                    │  shortcut
  in a single named pipe ───────────────────────┘                    ▼
                                    the last N segments ─► MP4: videos copied as they are,
                                                           "Mix" track + separate tracks
```

- FFmpeg forces an IDR keyframe every second: each segment stands on its own, so they can be joined without re-encoding.
- The `segment` muxer reuses the files (`segment_wrap`): the buffer's disk usage stays constant (length + 15 s).
- When saving, Snappy waits for the current segment to be closed (<= 1 s), picks the most recent segments whose total length does not exceed the limit, then remuxes them with a `-t limit` cap.
- Each audio track is captured in Python (WASAPI process loopback through `comtypes`, microphone through `soundcard`), padded with silence when nothing plays (WASAPI then delivers nothing) to stay in sync with the video, and the tracks are interleaved into a named pipe read by FFmpeg, which splits them.
- Audio capture runs in **a separate process**, at high priority, that does nothing else: Windows gives an application capture only a 10 ms buffer, and the slightest delay (interface, thumbnails…) would lose a packet, heard as a "click". Snappy sends it the list of applications to capture every 2 s. Each track keeps a 60 ms reserve and smoothly compensates the clock drift of audio outputs (at most ±0.5 % speed) instead of dropping sound.
- Windows can only exclude one application per capture: when several must be left out (chat, separate apps, audio mixer), the track adds up one capture per application that plays sound, looked up every 2 s.
- After repeated failures, Snappy automatically falls back to the GDI capture, then to the software encoder.
- FFmpeg is tied to Snappy (Windows "job" object): it stops with it, even after a crash; at startup, an FFmpeg left over from a previous session on the buffer folder is stopped.

## Structure

The code follows an MVC architecture, and everything that depends on the operating system is isolated in
`platforms/`:

```
snappy/
  app.py              entry point: log, single instance, creation of the main controller
  i18n.py             interface language: tr() and the French catalog (locales/fr.py)
  models/             data (no UI)
    settings.py       settings (settings.json) and tracks to record
    library.py        clip index, thumbnails, previews, trash, quota
    edit.py           edit of a clip: excerpt, cuts, multicam edit, mix, presets, undo
    games.py          running Steam game, installed games, cached artwork
  services/           engine (no UI)
    recorder.py       FFmpeg replay buffer (screens, tracks) and clips
    audio.py          audio tracks, buffers, resampling, delivery to FFmpeg
    audio_worker.py   dedicated audio process: captures the tracks and feeds FFmpeg
    audio_preview.py  multitrack playback of the editor
    media.py          export (cuts, multicam edit, mix, normalization, encoders), decoding
    ffmpeg.py         locating FFmpeg, probing, encoder choice
    jobs.py           background tasks: saving a clip, thumbnails, timeline frames
    sounds.py         confirmation sounds
  views/              Qt interface: shows the models, emits signals
    main_window.py    window, sidebar, gallery
    editor.py         editor (playback, timeline, export settings, sharing)
    timeline.py       timeline: excerpt, cuts, screens, tracks and volume curves
    settings_page.py  settings
    tray.py           notification area
    widgets.py, theme.py
  controllers/        connect views, models and services
    app.py            buffer, global shortcut, notification area, saving clips
    gallery.py        favorites, games, rename, trash, quick export, thumbnails
    editor.py         opening a clip, saving the edit, rename, export
    settings.py       system lists, applying the settings
  platforms/          system layer: `from snappy.platforms import system`
    windows/          Windows: folders, start at login, Recycle Bin, global shortcut (RegisterHotKey),
                      screen capture (ddagrab / gdigrab), WASAPI process loopback, named pipe,
                      "job" object, accent color, Steam
    linux/            Linux: gpu-screen-recorder replay (replay.py), PipeWire sources (capture.py),
                      KDE / GNOME / X11 shortcuts (hotkeys.py), XDG folders, autostart, trash
    generic.py        neutral fallback for other systems (starting point of a port)
  locales/            interface translations (fr.py: English text -> French text)
tests/                tests (python -m unittest discover tests)
```

Porting Snappy to another system means writing a `platforms/<system>/` module that offers the same functions as
`generic.py` (the list is at the top of `platforms/__init__.py`), then selecting it in that file: the rest of the
code never imports `platforms.windows` or `platforms.linux`. The replay engine itself is provided by the platform
(`replay_buffer_class()`): FFmpeg on Windows, gpu-screen-recorder on Linux, with the same interface and the same clip
format.

To translate the interface into another language, add a `snappy/locales/<code>.py` module with a `STRINGS`
dictionary (see `fr.py`) and add the code to `LANGUAGES` in `snappy/i18n.py`. Interface strings are written in
English in the code and wrapped in `tr()`.

## Data

- Settings and log: `%APPDATA%\Snappy\` (Linux: `~/.config/Snappy/`): `settings.json`, `snappy.log`
- Temporary buffer and caches (encoder benchmark, share MP3s): `%LOCALAPPDATA%\Snappy\`
  (Linux: `~/.cache/Snappy/`, including the replays while they are assembled)
- Clips: `Videos\Snappy\` by default (`.snappy-index.json` index, thumbnails in `.thumbs\`, exports in `Exports\`)

## Tests

```powershell
.\.venv\Scripts\python -m unittest discover tests -v
```

On Linux: `.venv/bin/python -m unittest discover tests -v`. `tests/test_linux.py` checks how the settings turn into
gpu-screen-recorder options, the desktop shortcuts and the assembly of a multi-screen replay; it also runs on
Windows. `tests/test_i18n.py` checks that every interface string has a French translation with the same
placeholders, and that no French text is left in the code.

The integration tests run a real FFmpeg with synthetic sources: they check that a clip never exceeds the limit, that a partial buffer gives a shorter clip, that the buffer's disk usage stays bounded, that two screens and three tracks (plus the Mix) are recorded, that restarting the buffer leaves a single FFmpeg, and that the exports (multicam edit, target size, Original, MP3) are correct.

## Known limitations

- The length precision is 1 s (one keyframe per second). A shorter interval is possible by changing `SEGMENT_SECONDS` in `services/recorder.py`, at the cost of a slightly higher bitrate.
- Very old exclusive full-screen games (DirectX 9) may not be captured by Desktop Duplication; borderless windowed mode always works.
- Frames go through the processor before encoding (the AMD driver refuses Desktop Duplication textures directly); each extra screen costs a little processor time.
- The microphone is the one chosen when the buffer starts; if you change headsets, apply the settings to restart the buffer.
- Linux: on GNOME with Wayland, the foreground application is not known (clips outside a Steam game keep the
  "Snappy …" name); with several screens, each one has its own recorder and the ends of the files are aligned (a gap
  of a few milliseconds is possible); the desktop portal only records one screen.
