#!/usr/bin/env bash
# Removes Snappy from the menu, the login items and the desktop shortcuts (Linux).
# The clips, the settings (~/.config/Snappy) and this folder are kept; --purge also removes
# the settings and the cache (never the videos).
set -uo pipefail
DATA="${XDG_DATA_HOME:-$HOME/.local/share}"
CONFIG="${XDG_CONFIG_HOME:-$HOME/.config}"

rm -f "$DATA/applications/snappy.desktop" "$DATA/applications/snappy-save.desktop" \
      "$DATA/icons/hicolor/256x256/apps/snappy.png" "$HOME/.local/bin/snappy" "$CONFIG/autostart/snappy.desktop"

# GNOME shortcut (KDE forgets it along with its .desktop file)
if command -v gsettings >/dev/null; then
    schema=org.gnome.settings-daemon.plugins.media-keys
    path=/org/gnome/settings-daemon/plugins/media-keys/custom-keybindings/snappy-save/
    current="$(gsettings get $schema custom-keybindings 2>/dev/null || true)"
    if [[ "$current" == *"$path"* ]]; then
        updated="$(python3 -c "import ast,sys; l=ast.literal_eval(sys.argv[1]); print([p for p in l if p!=sys.argv[2]])" "$current" "$path")"
        gsettings set $schema custom-keybindings "$updated"
        gsettings reset-recursively "$schema.custom-keybinding:$path" 2>/dev/null || true
    fi
fi

if [[ "${1:-}" == "--purge" ]]; then
    rm -rf "$CONFIG/Snappy" "${XDG_CACHE_HOME:-$HOME/.cache}/Snappy"
fi
echo "Snappy is uninstalled. Your clips stay in your Videos folder."
