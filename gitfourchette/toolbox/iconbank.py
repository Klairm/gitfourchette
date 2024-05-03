"""
If SVG icons don't show up, you may need to install the 'qt6-svg' package.
"""

from gitfourchette.qt import *
from gitfourchette.toolbox.qtutils import isDarkTheme

_stockIconCache = {}


class SvgIconEngine(QIconEngine):
    def __init__(self, data: bytes):
        super().__init__()
        self.data = data
        if HAS_QTSVG:
            self.renderer = QSvgRenderer(self.data)
            self.renderer.setAspectRatioMode(Qt.AspectRatioMode.KeepAspectRatio)
        else:
            self.renderer = None

    def paint(self, painter, rect, mode = QIcon.Mode.Normal, state = QIcon.State.On):
        if HAS_QTSVG:
            self.renderer.render(painter, QRectF(rect))


def assetCandidates(name: str):
    prefix = "assets:icons/"
    dark = isDarkTheme()
    for ext in ".svg", ".png":
        if dark:  # attempt to get dark mode variant first
            yield QFile(f"{prefix}{name}@dark{ext}")
        yield QFile(f"{prefix}{name}{ext}")


def getBestIconFile(name: str) -> str:
    try:
        f = next(f for f in assetCandidates(name) if f.exists())
        return f.fileName()
    except StopIteration:
        raise KeyError(f"no built-in icon asset '{name}'")


def lookUpNamedIcon(name: str) -> QIcon:
    try:
        # First, attempt to get a matching icon from the assets
        path = getBestIconFile(name)
        return QIcon(path)
    except KeyError:
        pass

    # Fall back to Qt standard icons (with "SP_" prefix)
    if name.startswith("SP_"):
        entry = getattr(QStyle.StandardPixmap, name)
        return QApplication.style().standardIcon(entry)

    # Fall back to theme icon
    return QIcon.fromTheme(name)


def remapSvgColors(name: str, clut: str) -> QIcon:
    path = getBestIconFile(name)
    with open(path, "rt", encoding="utf-8") as f:
        data = f.read()
    for pair in clut.split(";"):
        oldColor, newColor = pair.split("=")
        data = data.replace(oldColor, newColor)
    icon = QIcon(SvgIconEngine(data.encode("utf-8")))
    return icon


def stockIcon(iconId: str, clut="") -> QIcon:
    # Special cases
    if (MACOS or WINDOWS) and iconId == "achtung":
        iconId = "SP_MessageBoxWarning"

    if clut:
        key = f"{iconId}?{clut}"
    else:
        key = iconId

    # Attempt to get cached icon
    if key in _stockIconCache:
        return _stockIconCache[key]

    if clut:
        icon = remapSvgColors(iconId, clut)
    else:
        icon = lookUpNamedIcon(iconId)

    # Save icon in cache
    _stockIconCache[key] = icon
    return icon


def clearStockIconCache():
    _stockIconCache.clear()
