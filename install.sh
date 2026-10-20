#!/usr/bin/env bash
# Installs Snappy for the current user (Linux).
#   ./install.sh            dependencies (installed right away on Arch and derivatives), Python environment,
#                           applications menu entry, "snappy" command
#   ./install.sh --force    installs even when dependencies are missing
# The password is only asked for to install missing packages.
set -euo pipefail

SRC="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DATA="${XDG_DATA_HOME:-$HOME/.local/share}"
APPS="$DATA/applications"
ICONS="$DATA/icons/hicolor/256x256/apps"
BIN="$HOME/.local/bin"
FORCE=0; [[ "${1:-}" == "--force" ]] && FORCE=1

say() { printf '\033[1m%s\033[0m\n' "$*"; }

# ---------------------------------------------------------------- dependencies
ARCH_PKGS=(gpu-screen-recorder ffmpeg python pipewire libpulse xcb-util-cursor)

check_deps() {
    missing=()
    if ! command -v gpu-screen-recorder >/dev/null \
        && ! { command -v flatpak >/dev/null && flatpak info com.dec05eba.gpu_screen_recorder >/dev/null 2>&1; }; then
        missing+=("gpu-screen-recorder")
    fi
    for cmd in ffmpeg ffprobe python3; do
        command -v "$cmd" >/dev/null || missing+=("$cmd")
    done
    python3 - <<'EOF' 2>/dev/null || missing+=("python >= 3.10 with venv")
import sys, venv, ensurepip  # noqa
assert sys.version_info >= (3, 10)
EOF
}

check_deps
. /etc/os-release 2>/dev/null || true
case " ${ID:-} ${ID_LIKE:-} " in *" arch "*) IS_ARCH=1 ;; *) IS_ARCH=0 ;; esac

# Arch and derivatives (CachyOS, Manjaro, EndeavourOS...): everything is in the official repositories.
if ((${#missing[@]})) && ((IS_ARCH)); then
    say "Installing the dependencies (your password will be asked)..."
    sudo pacman -S --needed "${ARCH_PKGS[@]}" || say "Installing the packages failed."
    check_deps
fi

if ((${#missing[@]})); then
    say "Missing dependencies: ${missing[*]}"
    case " ${ID:-} ${ID_LIKE:-} " in
        *" arch "*)
            echo "  sudo pacman -S --needed ${ARCH_PKGS[*]}" ;;
        *" fedora "*|*" rhel "*)
            echo "  sudo dnf install ffmpeg python3 pipewire-utils xcb-util-cursor"
            echo "  + gpu-screen-recorder: flatpak install flathub com.dec05eba.gpu_screen_recorder" ;;
        *" debian "*|*" ubuntu "*)
            echo "  sudo apt install ffmpeg python3-venv pipewire-bin libxcb-cursor0"
            echo "  + gpu-screen-recorder: flatpak install flathub com.dec05eba.gpu_screen_recorder" ;;
        *" opensuse"*|*" suse "*)
            echo "  sudo zypper install ffmpeg python3 pipewire-tools gpu-screen-recorder" ;;
        *) echo "  Install these packages with your distribution's package manager." ;;
    esac
    ((FORCE)) || { echo "Run again with --force to install anyway."; exit 1; }
fi

# ---------------------------------------------------------------- Python environment
say "Python environment (.venv)..."
[[ -x "$SRC/.venv/bin/python" ]] || python3 -m venv "$SRC/.venv"
"$SRC/.venv/bin/python" -m pip install --quiet --upgrade pip
"$SRC/.venv/bin/python" -m pip install --quiet -r "$SRC/requirements.txt"
chmod +x "$SRC/run.sh" "$SRC/install.sh" "$SRC/uninstall.sh" 2>/dev/null || true

# ---------------------------------------------------------------- applications menu
mkdir -p "$APPS" "$ICONS" "$BIN"
cp "$SRC/assets/snappy.png" "$ICONS/snappy.png"
ln -sf "$SRC/run.sh" "$BIN/snappy"
cat > "$APPS/snappy.desktop" <<EOF
[Desktop Entry]
Type=Application
Name=Snappy
GenericName=Game clips
Comment=Instant replay of your screen
Exec="$SRC/run.sh"
Icon=snappy
Terminal=false
Categories=AudioVideo;Video;Recorder;
StartupWMClass=snappy
EOF
command -v update-desktop-database >/dev/null && update-desktop-database "$APPS" 2>/dev/null || true

say "Snappy is installed. Start it from the applications menu or with the \"snappy\" command."
case ":$PATH:" in
    *":$BIN:"*) ;;
    *) echo "The \"snappy\" command will be available at your next login ($BIN is not in the PATH yet)."
       echo "Until then: $SRC/run.sh" ;;
esac
echo "On first launch, Snappy installs its shortcut (Alt+F10) in KDE or GNOME;"
echo "elsewhere, the settings show the command to bind to the shortcut of your choice."
