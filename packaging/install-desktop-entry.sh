#!/bin/sh
# Install the app-menu entry for the current user.
#   --autostart   also start Smart Control in the tray when you log in
set -e
dir="${XDG_DATA_HOME:-$HOME/.local/share}/applications"
autostart_dir="${XDG_CONFIG_HOME:-$HOME/.config}/autostart"
mkdir -p "$dir"
src="$(dirname "$0")/smartcontrol.desktop"
icon_dir="${XDG_DATA_HOME:-$HOME/.local/share}/icons/hicolor/scalable/apps"
mkdir -p "$icon_dir"
cp "$(dirname "$0")/../smartcontrol/icons/earbuds.svg" "$icon_dir/smartcontrol.svg"
# -t: a per-user hicolor dir has no index.theme, which otherwise makes this a no-op
gtk-update-icon-cache -q -f -t "${icon_dir%/scalable/apps}" 2>/dev/null || true
if command -v smartcontrol-gui >/dev/null 2>&1; then
    cmd="smartcontrol-gui"
else
    # not pip-installed: run from the checkout
    repo="$(cd "$(dirname "$0")/.." && pwd)"
    cmd="env -C \"$repo\" python3 -m smartcontrol.gui"
fi
# The app-menu entry opens the window (or the running instance's window).
sed "s|^Exec=.*|Exec=$cmd --show|" "$src" > "$dir/smartcontrol.desktop"
update-desktop-database "$dir" 2>/dev/null || true
echo "Installed $dir/smartcontrol.desktop"
if [ "$1" = "--autostart" ]; then
    mkdir -p "$autostart_dir"
    sed "s|^Exec=.*|Exec=$cmd|" "$src" > "$autostart_dir/smartcontrol.desktop"
    echo "Installed $autostart_dir/smartcontrol.desktop (starts in the tray at login)"
fi
