"""Exports Snappy's icon (drawn in code): .ico (Windows) or .png (Linux), from the extension."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from PySide6.QtGui import QGuiApplication  # noqa: E402

from snappy.views.widgets import app_icon  # noqa: E402

out = Path(sys.argv[1] if len(sys.argv) > 1 else "assets/snappy.ico")
out.parent.mkdir(parents=True, exist_ok=True)
app = QGuiApplication([])
# Qt writes an .ico from the largest image; 256 px covers every use.
pixmap = app_icon(256).pixmap(256, 256)
if out.suffix.lower() == ".png":
    pixmap.save(str(out), "PNG")
    print(f"Icon written: {out}")
elif not pixmap.save(str(out), "ICO"):
    pixmap.save(str(out.with_suffix(".png")), "PNG")
    print("ICO format unavailable: PNG written instead")
else:
    print(f"Icon written: {out}")
