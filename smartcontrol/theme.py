"""Icons, and detection of whether the tray/panel background is dark."""
from __future__ import annotations

import os
from pathlib import Path

from PySide6.QtCore import QFileSystemWatcher, QObject, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QGuiApplication, QIcon, QPainter, QPixmap

ICONS = Path(__file__).with_name("icons")
_CONFIG_HOME = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config"))
_TRAY_SIZES = (16, 22, 24, 32, 48, 64, 128)
ICON_STYLE = "earbuds"          # or "headphones"; both sets live in icons/


def app_icon() -> QIcon:
    return QIcon(str(ICONS / f"{ICON_STYLE}.svg"))


def tray_icon(white: bool, opacity: float = 1.0) -> QIcon:
    """The monochrome glyph: white for dark panels, black for light ones."""
    src = QIcon(str(ICONS / f"tray-{ICON_STYLE}-{'white' if white else 'black'}.svg"))
    if opacity >= 1.0:
        return src
    icon = QIcon()
    for size in _TRAY_SIZES:
        pm = QPixmap(size, size)
        pm.fill(Qt.transparent)
        p = QPainter(pm)
        p.setOpacity(opacity)
        src.paint(p, 0, 0, size, size)
        p.end()
        icon.addPixmap(pm)
    return icon


def _ini_value(path: Path, section: str, key: str) -> str | None:
    """Minimal KConfig reader (configparser trips over KDE's [$e]-style keys)."""
    try:
        lines = path.read_text(errors="replace").splitlines()
    except OSError:
        return None
    current = None
    for line in lines:
        line = line.strip()
        if line.startswith("["):
            current = line.strip("[]")
        elif current == section and "=" in line:
            k, v = line.split("=", 1)
            if k.split("[", 1)[0].strip() == key:
                return v.strip()
    return None


def _is_dark(c: QColor) -> bool:
    return 0.2126 * c.redF() + 0.7152 * c.greenF() + 0.0722 * c.blueF() < 0.5


def _kde_panel_dark() -> bool | None:
    # The Plasma style decides the panel colour; "default" (Breeze) follows the colour scheme.
    theme = (_ini_value(_CONFIG_HOME / "plasmarc", "Theme", "name") or "default").lower()
    if "dark" in theme:
        return True
    if "light" in theme:
        return False
    rgb = _ini_value(_CONFIG_HOME / "kdeglobals", "Colors:Window", "BackgroundNormal")
    try:
        r, g, b = (int(x) for x in rgb.split(",")[:3])
    except (AttributeError, ValueError):
        return None
    return _is_dark(QColor(r, g, b))


def tray_background_dark() -> bool:
    desktop = os.environ.get("XDG_CURRENT_DESKTOP", "").upper()
    if "KDE" in desktop:
        dark = _kde_panel_dark()
        if dark is not None:
            return dark
    if "GNOME" in desktop or "UNITY" in desktop:
        return True             # the GNOME top bar is dark in both light and dark mode
    scheme = QGuiApplication.styleHints().colorScheme()
    if scheme != Qt.ColorScheme.Unknown:
        return scheme == Qt.ColorScheme.Dark
    return _is_dark(QGuiApplication.palette().window().color())


class TrayThemeWatcher(QObject):
    """Emits changed(dark) when the panel background switches between light and dark."""
    changed = Signal(bool)

    def __init__(self, parent: QObject | None = None):
        super().__init__(parent)
        self.dark = tray_background_dark()
        self._files = [str(_CONFIG_HOME / "kdeglobals"), str(_CONFIG_HOME / "plasmarc")]
        self._watcher = QFileSystemWatcher(self)
        self._watcher.fileChanged.connect(self._schedule)
        self._watch()
        self._debounce = QTimer(self, singleShot=True, interval=300)
        self._debounce.timeout.connect(self._recheck)
        QGuiApplication.styleHints().colorSchemeChanged.connect(self._schedule)

    def _watch(self):
        # KConfig replaces files on save, which drops them from the watcher: re-add every time.
        missing = [f for f in self._files if f not in self._watcher.files() and os.path.exists(f)]
        if missing:
            self._watcher.addPaths(missing)

    def _schedule(self, *_):
        self._debounce.start()

    def _recheck(self):
        self._watch()
        dark = tray_background_dark()
        if dark != self.dark:
            self.dark = dark
            self.changed.emit(dark)
