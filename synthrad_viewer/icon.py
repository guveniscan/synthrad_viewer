"""App icon, drawn with QPainter so no binary assets are needed.

``python -m synthrad_viewer.icon OUT.iconset`` writes the PNG sizes ``iconutil`` expects.
"""
from __future__ import annotations

import sys
from pathlib import Path

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import (QBrush, QColor, QIcon, QImage, QLinearGradient, QPainter,
                           QPainterPath, QPen, QPixmap, QRadialGradient)


def render(size: int) -> QImage:
    img = QImage(size, size, QImage.Format.Format_ARGB32_Premultiplied)
    img.fill(Qt.GlobalColor.transparent)
    p = QPainter(img)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    s = size / 1024.0

    # macOS-style rounded square
    tile = QRectF(100 * s, 100 * s, 824 * s, 824 * s)
    bg = QLinearGradient(tile.topLeft(), tile.bottomLeft())
    bg.setColorAt(0, QColor("#2b3a4f"))
    bg.setColorAt(1, QColor("#0e141c"))
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(QBrush(bg))
    p.drawRoundedRect(tile, 185 * s, 185 * s)

    # axial body cross-section, left half MRI-ish (warm grey), right half CT (cool grey)
    body = QRectF(215 * s, 300 * s, 594 * s, 440 * s)
    path = QPainterPath()
    path.addEllipse(body)
    for half, colour in ((QRectF(0, 0, 512 * s, size), "#c9c2b8"),
                         (QRectF(512 * s, 0, 512 * s, size), "#aebccc")):
        p.save()
        p.setClipRect(half)
        g = QRadialGradient(body.center(), body.width() / 1.6)
        g.setColorAt(0, QColor(colour))
        g.setColorAt(1, QColor(colour).darker(170))
        p.setBrush(QBrush(g))
        p.drawPath(path)
        p.restore()

    # spine and two lungs/kidneys as dark/bright structures
    p.setBrush(QColor("#f4f1ea"))
    p.drawEllipse(QPointF(512 * s, 640 * s), 52 * s, 46 * s)
    p.setBrush(QColor("#3a4350"))
    p.drawEllipse(QPointF(390 * s, 500 * s), 88 * s, 120 * s)
    p.drawEllipse(QPointF(634 * s, 500 * s), 88 * s, 120 * s)

    # crosshair accent
    pen = QPen(QColor("#ffc83d"), 22 * s)
    pen.setCapStyle(Qt.PenCapStyle.RoundCap)
    p.setPen(pen)
    p.drawLine(QPointF(512 * s, 215 * s), QPointF(512 * s, 820 * s))
    p.end()
    return img


def app_icon() -> QIcon:
    icon = QIcon()
    for size in (16, 32, 64, 128, 256, 512, 1024):
        icon.addPixmap(QPixmap.fromImage(render(size)))
    return icon


def write_iconset(out: Path) -> None:
    out.mkdir(parents=True, exist_ok=True)
    for size in (16, 32, 128, 256, 512):
        render(size).save(str(out / f"icon_{size}x{size}.png"))
        render(size * 2).save(str(out / f"icon_{size}x{size}@2x.png"))


if __name__ == "__main__":
    from PySide6.QtGui import QGuiApplication

    app = QGuiApplication(sys.argv[:1] + ["-platform", "offscreen"])
    write_iconset(Path(sys.argv[1]))
