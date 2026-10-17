"""One image panel: a slice with PACS-style corner labels, crosshair and mask overlay.

Mouse: wheel scrolls slices, click sets the cursor, pinch or ⌘-scroll zooms, drag pans,
right-drag adjusts brightness/contrast, double-click fits the image.
"""
from __future__ import annotations

import numpy as np
import pyqtgraph as pg
from PySide6.QtCore import QEvent, QRectF, Qt, Signal
from PySide6.QtWidgets import QLabel, QVBoxLayout, QWidget

from .volume import ORIENTATION_LABELS, PLANE_AXIS, Volume

ACCENT = "#ffc83d"
MASK_COLOUR = (255, 90, 95)
_ZOOM_MODIFIERS = (Qt.KeyboardModifier.ControlModifier | Qt.KeyboardModifier.MetaModifier
                   | Qt.KeyboardModifier.AltModifier)


class SliceViewBox(pg.ViewBox):
    sliceScrolled = Signal(int)
    clicked = Signal(object)  # scene position
    windowDragStarted = Signal()
    windowDragged = Signal(float, float)
    fitRequested = Signal()

    def __init__(self):
        super().__init__(lockAspect=True, invertY=True, enableMenu=False)
        self._wheel = 0.0

    def wheelEvent(self, ev, axis=None):
        if ev.modifiers() & _ZOOM_MODIFIERS:
            super().wheelEvent(ev, axis)
            return
        ev.accept()
        if ev.orientation() != Qt.Orientation.Vertical:
            return
        delta = ev.delta()
        if abs(delta) >= 120 and self._wheel == 0:  # mouse wheel notch
            steps = int(delta / 120)
        else:  # trackpad: accumulate small deltas
            self._wheel += delta
            steps = int(self._wheel / 40)
            self._wheel -= steps * 40
        if steps:
            self.sliceScrolled.emit(steps)

    def mouseDragEvent(self, ev, axis=None):
        if ev.button() == Qt.MouseButton.RightButton:
            ev.accept()
            if ev.isStart():
                self.windowDragStarted.emit()
            d = ev.screenPos() - ev.buttonDownScreenPos()
            self.windowDragged.emit(d.x(), d.y())
            return
        super().mouseDragEvent(ev, axis)

    def mouseClickEvent(self, ev):
        if ev.button() == Qt.MouseButton.LeftButton:
            ev.accept()
            if ev.double():
                self.fitRequested.emit()
            else:
                self.clicked.emit(ev.scenePos())
            return
        super().mouseClickEvent(ev)


def _overlay_label(parent, size=12, bold=False, color="rgba(255,255,255,0.82)"):
    label = QLabel(parent)
    label.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
    weight = 600 if bold else 400
    label.setStyleSheet(f"color: {color}; background: transparent; "
                        f"font-size: {size}px; font-weight: {weight};")
    return label


def mask_overlay(mask: np.ndarray, mode: str) -> np.ndarray | None:
    """RGBA image for a 2-D boolean mask slice: 'outline', 'filled' or 'dim' (outside)."""
    if mode == "off":
        return None
    rgba = np.zeros(mask.shape + (4,), np.uint8)
    if mode == "dim":
        rgba[~mask, 3] = 165
        return rgba
    if mode == "filled":
        rgba[mask] = (*MASK_COLOUR, 70)
        return rgba
    inner = mask.copy()
    inner[1:, :] &= mask[:-1, :]
    inner[:-1, :] &= mask[1:, :]
    inner[:, 1:] &= mask[:, :-1]
    inner[:, :-1] &= mask[:, 1:]
    rgba[mask & ~inner] = (*MASK_COLOUR, 255)
    return rgba


class SlicePanel(QWidget):
    hovered = Signal(object)  # voxel (x, y, z) or None
    clicked = Signal(object)  # voxel (x, y, z)
    sliceScrolled = Signal(int)
    levelsEdited = Signal(float, float)
    fitRequested = Signal()

    def __init__(self, plane: str, parent=None):
        super().__init__(parent)
        self.plane = plane
        self.volume: Volume | None = None  # geometry of what is shown
        self.index = 0
        self.levels = (0.0, 1.0)
        self.level_style = "window"  # "window", "symmetric" (± range) or "none"
        self._drag_levels = self.levels

        self.view = pg.GraphicsView(background="#000000")
        self.view.setFocusPolicy(Qt.FocusPolicy.ClickFocus)
        self.vb = SliceViewBox()
        self.view.setCentralItem(self.vb)
        self.image = pg.ImageItem()
        self.vb.addItem(self.image)
        self.overlay = pg.ImageItem()
        self.overlay.setZValue(5)
        self.vb.addItem(self.overlay, ignoreBounds=True)
        line_pen = pg.mkPen(255, 200, 61, 150, width=1)
        self.hline = pg.InfiniteLine(angle=0, movable=False, pen=line_pen)
        self.vline = pg.InfiniteLine(angle=90, movable=False, pen=line_pen)
        for line in (self.hline, self.vline):
            line.setZValue(8)
            line.hide()
            self.vb.addItem(line, ignoreBounds=True)
        self.marker = pg.ScatterPlotItem(size=18, symbol="+", pen=pg.mkPen(ACCENT, width=1.5),
                                         brush=pg.mkBrush(0, 0, 0, 0))
        self.marker.setZValue(10)
        self.marker.hide()
        self.vb.addItem(self.marker, ignoreBounds=True)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self.view)

        self.title = _overlay_label(self.view, size=14, bold=True, color="#ffffff")
        self.title.setTextFormat(Qt.TextFormat.RichText)
        self.legend = _overlay_label(self.view, size=12)
        self.legend.setTextFormat(Qt.TextFormat.RichText)
        self.window_label = _overlay_label(self.view)
        self.slice_label = _overlay_label(self.view)
        self.orientation = {side: _overlay_label(self.view, size=13, bold=True, color=ACCENT)
                            for side in ("left", "right", "top", "bottom")}
        self.message = _overlay_label(self.view, size=15, color="rgba(255,255,255,0.55)")
        self.message.setAlignment(Qt.AlignmentFlag.AlignCenter)

        self.vb.sliceScrolled.connect(self.sliceScrolled)
        self.vb.fitRequested.connect(self.fitRequested)
        self.vb.clicked.connect(self._clicked)
        self.vb.windowDragStarted.connect(self._start_window_drag)
        self.vb.windowDragged.connect(self._window_drag)
        self.view.scene().sigMouseMoved.connect(self._mouse_moved)
        self.view.installEventFilter(self)
        self.view.viewport().installEventFilter(self)

    # -- content -------------------------------------------------------------------------

    def set_title(self, html: str, legend: str = "") -> None:
        self.title.setText(html)
        self.legend.setText(legend)
        self.legend.setVisible(bool(legend))
        self._layout_overlay()

    def set_message(self, text: str | None) -> None:
        self.message.setText(text or "")
        self.message.setVisible(bool(text))
        for item in (self.image, self.overlay):
            item.setVisible(not text)
        if text:
            self.legend.hide()
            for item in (self.marker, self.hline, self.vline):
                item.hide()
            self.slice_label.clear()
            self.window_label.clear()
            for label in self.orientation.values():
                label.clear()
        self._layout_overlay()

    @property
    def showing_image(self) -> bool:
        return self.volume is not None and not self.message.isVisible()

    def set_content(self, volume: Volume, index: int, image: np.ndarray,
                    overlay: np.ndarray | None = None, lut=None) -> None:
        """Show ``image`` (a slice of ``volume``'s grid, or an image derived from one)."""
        self.set_message(None)
        self.volume, self.index = volume, index
        rgb = image.ndim == 3
        self.image.setLookupTable(None if rgb else lut)
        self.image.setImage(image, autoLevels=False,
                            levels=(0.0, 1.0) if rgb else self.levels)
        width, height = volume.extent_mm(self.plane)
        rect = QRectF(0, 0, width, height)
        self.image.setRect(rect)
        if overlay is None:
            self.overlay.clear()
        else:
            self.overlay.setImage(overlay, autoLevels=False, levels=(0, 255))
            self.overlay.setRect(rect)
        self.slice_label.setText(f"{self.plane.capitalize()}  ·  slice {index + 1} of "
                                 f"{volume.n_slices(self.plane)}")
        for side, text in ORIENTATION_LABELS[self.plane].items():
            self.orientation[side].setText(text)
        self._layout_overlay()

    def set_levels(self, lo: float, hi: float) -> None:
        self.levels = (lo, hi)
        if self.image.image is not None and self.image.image.ndim == 2:
            self.image.setLevels(self.levels)
        if self.level_style == "window":
            text = f"W {hi - lo:.0f}   L {(hi + lo) / 2:.0f}"
        elif self.level_style == "symmetric":
            text = f"± {hi:.0f} HU"
        else:
            text = ""
        self.window_label.setText(text.replace("-", "−"))
        self._layout_overlay()

    def fit(self) -> None:
        self.vb.autoRange(items=[self.image], padding=0.02)

    def on_slice(self, xyz) -> bool:
        return (self.volume is not None and self.volume.contains(xyz)
                and xyz[PLANE_AXIS[self.plane]] == self.index)

    def show_marker(self, xyz) -> None:
        if xyz is None or not self.showing_image or not self.on_slice(xyz):
            self.marker.hide()
            return
        u, v = self.volume.from_voxel(self.plane, xyz)
        self.marker.setData([u], [v])
        self.marker.show()

    def set_crosshair(self, xyz) -> None:
        if xyz is None or not self.showing_image:
            self.hline.hide()
            self.vline.hide()
            return
        u, v = self.volume.from_voxel(self.plane, xyz)
        self.vline.setPos(u)
        self.hline.setPos(v)
        self.vline.show()
        self.hline.show()

    # -- interaction -------------------------------------------------------------------

    def _voxel_at(self, scene_pos):
        if not self.showing_image or not self.vb.sceneBoundingRect().contains(scene_pos):
            return None
        p = self.vb.mapSceneToView(scene_pos)
        return self.volume.to_voxel(self.plane, self.index, p.x(), p.y())

    def _mouse_moved(self, scene_pos) -> None:
        if self.showing_image:
            self.hovered.emit(self._voxel_at(scene_pos))

    def _clicked(self, scene_pos) -> None:
        xyz = self._voxel_at(scene_pos)
        if xyz is not None:
            self.clicked.emit(xyz)

    def _start_window_drag(self) -> None:
        self._drag_levels = self.levels

    def _window_drag(self, dx: float, dy: float) -> None:
        if self.level_style == "none" or not self.showing_image:
            return
        lo, hi = self._drag_levels
        width, level = hi - lo, (hi + lo) / 2
        step = max(width, 20.0) / 250.0
        width = max(1.0, width + dx * step)
        level = level + dy * step
        self.levelsEdited.emit(level - width / 2, level + width / 2)

    def eventFilter(self, obj, ev):
        t = ev.type()
        if obj is self.view and t == QEvent.Type.Resize:
            self._layout_overlay()
        elif obj is self.view.viewport():
            if t == QEvent.Type.Leave:
                self.hovered.emit(None)
            elif (t == QEvent.Type.NativeGesture
                  and ev.gestureType() == Qt.NativeGestureType.ZoomNativeGesture):
                centre = self.vb.mapSceneToView(self.view.mapToScene(ev.position().toPoint()))
                s = 1.0 / (1.0 + ev.value())
                self.vb.scaleBy((s, s), center=centre)
                return True
        return super().eventFilter(obj, ev)

    def _layout_overlay(self) -> None:
        w, h, m = self.view.width(), self.view.height(), 12
        for label in (self.title, self.legend, self.window_label, self.slice_label,
                      self.message, *self.orientation.values()):
            label.adjustSize()
        self.title.move(m, m)
        self.legend.move(m, m + self.title.height() + 2)
        self.window_label.move(w - self.window_label.width() - m, m)
        self.slice_label.move(m, h - self.slice_label.height() - m)
        o = self.orientation
        o["left"].move(m, (h - o["left"].height()) // 2)
        o["right"].move(w - o["right"].width() - m, (h - o["right"].height()) // 2)
        o["top"].move((w - o["top"].width()) // 2, m)
        o["bottom"].move((w - o["bottom"].width()) // 2, h - o["bottom"].height() - m)
        self.message.move((w - self.message.width()) // 2, (h - self.message.height()) // 2)
