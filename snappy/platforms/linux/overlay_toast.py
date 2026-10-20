"""Wayland toast over the game: a small standalone GTK 4 window in the layer-shell "overlay" layer.

On Wayland a regular window cannot choose its place nor stay above a full-screen game, but the
layer-shell protocol (KDE, Hyprland, Sway... not GNOME) can. Qt has no Python binding for it, so
Snappy runs this script with the system Python, which has PyGObject and gtk4-layer-shell:

    python3 overlay_toast.py <title> <subtitle> <ok: 1|0> <ms> [monitor connector] [thumbnail]

The window never takes the keyboard focus and lets the clicks through: the game is not disturbed.
Exit code 0 once shown, 3 if GTK / gtk4-layer-shell are missing (Snappy then falls back to a
desktop notification).
"""

import sys
from ctypes import CDLL

try:
    CDLL("libgtk4-layer-shell.so.0")   # must be loaded before GTK
    import gi

    gi.require_version("Gtk", "4.0")
    gi.require_version("Gdk", "4.0")
    gi.require_version("Gtk4LayerShell", "1.0")
    gi.require_version("GdkPixbuf", "2.0")
    import cairo
    from gi.repository import Gdk, GdkPixbuf, GLib, Gtk
    from gi.repository import Gtk4LayerShell as LayerShell
except (OSError, ImportError, ValueError):
    sys.exit(3)

CSS = b"""
window { background: transparent; }
.toast { background: rgba(24, 24, 28, 0.94); color: white; border-radius: 14px; padding: 10px 16px 10px 10px;
         border: 1px solid rgba(255, 255, 255, 0.08); }
.toast .title { font-weight: 800; font-size: 1.05em; }
.toast .sub { opacity: 0.75; font-size: 0.9em; }
.toast .dot { border-radius: 999px; min-width: 12px; min-height: 12px; margin: 6px; }
.toast .dot.ok { background: #2ec27e; }
.toast .dot.error { background: #e01b24; }
.toast picture { border-radius: 8px; }
"""
FADE_MS = 160


def main() -> int:
    title, subtitle, ok, ms = sys.argv[1], sys.argv[2], sys.argv[3] == "1", int(sys.argv[4])
    connector = sys.argv[5] if len(sys.argv) > 5 else ""
    thumb = sys.argv[6] if len(sys.argv) > 6 else ""
    if not Gtk.init_check() or not LayerShell.is_supported():
        return 3
    css = Gtk.CssProvider()
    css.load_from_data(CSS)
    Gtk.StyleContext.add_provider_for_display(Gdk.Display.get_default(), css, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)

    win = Gtk.Window(title="Snappy")
    LayerShell.init_for_window(win)
    LayerShell.set_namespace(win, "snappy-toast")
    LayerShell.set_layer(win, LayerShell.Layer.OVERLAY)
    LayerShell.set_keyboard_mode(win, LayerShell.KeyboardMode.NONE)
    for edge in (LayerShell.Edge.TOP, LayerShell.Edge.RIGHT):
        LayerShell.set_anchor(win, edge, True)
        LayerShell.set_margin(win, edge, 28)
    monitors = Gdk.Display.get_default().get_monitors()
    for i in range(monitors.get_n_items()):
        if monitors.get_item(i).get_connector() == connector:   # the recorded screen (the game's)
            LayerShell.set_monitor(win, monitors.get_item(i))

    box = Gtk.Box(spacing=12, css_classes=["toast"])
    texture = None
    if thumb:
        try:
            # Scaled when loaded: a Gtk.Picture asks for the natural size of its image.
            scale = max(1, win.get_scale_factor())
            pixbuf = GdkPixbuf.Pixbuf.new_from_file_at_scale(thumb, 96 * scale, 54 * scale, True)
            texture = Gdk.Texture.new_for_pixbuf(pixbuf)
        except GLib.Error:
            texture = None
    if texture:
        box.append(Gtk.Picture(paintable=texture, width_request=96, height_request=54, can_shrink=True,
                               content_fit=Gtk.ContentFit.COVER, valign=Gtk.Align.CENTER))
    else:
        box.append(Gtk.Box(css_classes=["dot", "ok" if ok else "error"], valign=Gtk.Align.CENTER))
    text = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, valign=Gtk.Align.CENTER)
    text.append(Gtk.Label(label=title, xalign=0, css_classes=["title"]))
    if subtitle:
        text.append(Gtk.Label(label=subtitle, xalign=0, css_classes=["sub"], max_width_chars=48, ellipsize=3))
    box.append(text)
    win.set_child(box)

    def click_through(*_):
        surface = win.get_surface()
        if surface:
            surface.set_input_region(cairo.Region())

    win.connect("realize", click_through)
    win.set_opacity(0)
    win.present()

    loop = GLib.MainLoop()
    state = {"t": 0}

    def fade():
        state["t"] += 16
        t = state["t"]
        if t < FADE_MS:
            win.set_opacity(t / FADE_MS)
        elif t < ms:
            win.set_opacity(1)
        elif t < ms + 2 * FADE_MS:
            win.set_opacity(1 - (t - ms) / (2 * FADE_MS))
        else:
            win.destroy()
            loop.quit()
            return False
        return True

    GLib.timeout_add(16, fade)
    loop.run()
    return 0


if __name__ == "__main__":
    sys.exit(main())
