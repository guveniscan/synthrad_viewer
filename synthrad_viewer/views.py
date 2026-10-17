"""One image panel: a slice with PACS-style corner labels.

Mouse: wheel scrolls slices, pinch or ⌘-scroll zooms, drag pans, right-drag adjusts
brightness/contrast, double-click fits the image.
"""
from __future__ import annotations

import pyqtgraph as pg
from PySide6.QtCore import QEvent, QRectF, Qt, Signal
from PySide6.QtWidgets import QLabel, QVBoxLayout, QWidget

from .volume import ORIENTATION_LABELS, Volume

ACCENT = "#ffc83d"
_ZOOM_MODIFIERS = (Qt.KeyboardModifier.ControlModifier | Qt.KeyboardModifier.MetaModifier
                   | Qt.KeyboardModifier.AltModifier)


class SliceViewBox(pg.ViewBox):
    sliceScrolled = Signal(int)
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
        if ev.double() and ev.button() == Qt.MouseButton.LeftButton:
            ev.accept()
            self.fitRequested.emit()
            return
        super().mouseClickEvent(ev)


def _overlay_label(parent, size=12, bold=False, color="rgba(255,255,255,0.82)"):
    label = QLabel(parent)
    label.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
    weight = 600 if bold else 400
    label.setStyleSheet(f"color: {color}; background: transparent; "
                        f"font-size: {size}px; font-weight: {weight};")
    return label


class SlicePanel(QWidget):
    hovered = Signal(object)  # voxel (x, y, z) or None
    sliceScrolled = Signal(int)
    levelsEdited = Signal()
    fitRequested = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.volume: Volume | None = None
        self.plane = "axial"
        self.index = 0
        self.levels = (0.0, 1.0)
        self._drag_levels = self.levels

        self.view = pg.GraphicsView(background="#000000")
        self.view.setFocusPolicy(Qt.FocusPolicy.ClickFocus)
        self.vb = SliceViewBox()
        self.view.setCentralItem(self.vb)
        self.image = pg.ImageItem()
        self.vb.addItem(self.image)
        self.marker = pg.ScatterPlotItem(size=18, symbol="+", pen=pg.mkPen(ACCENT, width=1.5),
                                         brush=pg.mkBrush(0, 0, 0, 0))
        self.marker.setZValue(10)
        self.marker.hide()
        self.vb.addItem(self.marker, ignoreBounds=True)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self.view)

        self.title = _overlay_label(self.view, size=14, bold=True, color="#ffffff")
        self.window_label = _overlay_label(self.view)
        self.slice_label = _overlay_label(self.view)
        self.orientation = {side: _overlay_label(self.view, size=13, bold=True, color=ACCENT)
                            for side in ("left", "right", "top", "bottom")}
        self.message = _overlay_label(self.view, size=15, color="rgba(255,255,255,0.55)")
        self.message.setAlignment(Qt.AlignmentFlag.AlignCenter)

        self.vb.sliceScrolled.connect(self.sliceScrolled)
        self.vb.fitRequested.connect(self.fitRequested)
        self.vb.windowDragStarted.connect(self._start_window_drag)
        self.vb.windowDragged.connect(self._window_drag)
        self.view.scene().sigMouseMoved.connect(self._mouse_moved)
        self.view.installEventFilter(self)
        self.view.viewport().installEventFilter(self)

    # -- content -------------------------------------------------------------------------

    def set_title(self, text: str) -> None:
        self.title.setText(text)
        self._layout_overlay()

    def set_message(self, text: str | None) -> None:
        self.message.setText(text or "")
        self.message.setVisible(bool(text))
        self.image.setVisible(not text)
        if text:
            self.marker.hide()
            self.slice_label.clear()
            self.window_label.clear()
            for label in self.orientation.values():
                label.clear()
        self._layout_overlay()

    def set_content(self, volume: Volume, plane: str, index: int) -> None:
        self.volume, self.plane, self.index = volume, plane, index
        self.image.setImage(volume.slice(plane, index), autoLevels=False, levels=self.levels)
        width, height = volume.extent_mm(plane)
        self.image.setRect(QRectF(0, 0, width, height))
        self.slice_label.setText(f"{plane.capitalize()}  ·  slice {index + 1} of "
                                 f"{volume.n_slices(plane)}")
        for side, text in ORIENTATION_LABELS[plane].items():
            self.orientation[side].setText(text)
        self._layout_overlay()

    def set_levels(self, lo: float, hi: float) -> None:
        self.levels = (lo, hi)
        self.image.setLevels(self.levels)
        width, level = hi - lo, (hi + lo) / 2
        self.window_label.setText(f"W {width:.0f}   L {level:.0f}".replace("-", "−"))
        self._layout_overlay()

    def fit(self) -> None:
        self.vb.autoRange(items=[self.image], padding=0.02)

    def show_marker(self, xyz) -> None:
        if xyz is None or self.volume is None or not self.volume.contains(xyz):
            self.marker.hide()
            return
        u, v = self.volume.from_voxel(self.plane, xyz)
        self.marker.setData([u], [v])
        self.marker.show()

    # -- interaction -------------------------------------------------------------------

    def _mouse_moved(self, scene_pos) -> None:
        if self.volume is None or not self.image.isVisible():
            return
        if not self.vb.sceneBoundingRect().contains(scene_pos):
            self.hovered.emit(None)
            return
        p = self.vb.mapSceneToView(scene_pos)
        self.hovered.emit(self.volume.to_voxel(self.plane, self.index, p.x(), p.y()))

    def _start_window_drag(self) -> None:
        self._drag_levels = self.levels

    def _window_drag(self, dx: float, dy: float) -> None:
        lo, hi = self._drag_levels
        width, level = hi - lo, (hi + lo) / 2
        step = max(width, 20.0) / 250.0
        width = max(1.0, width + dx * step)
        level = level + dy * step
        self.set_levels(level - width / 2, level + width / 2)
        self.levelsEdited.emit()

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
        for label in (self.title, self.window_label, self.slice_label, self.message,
                      *self.orientation.values()):
            label.adjustSize()
        self.title.move(m, m)
        self.window_label.move(w - self.window_label.width() - m, m)
        self.slice_label.move(m, h - self.slice_label.height() - m)
        o = self.orientation
        o["left"].move(m, (h - o["left"].height()) // 2)
        o["right"].move(w - o["right"].width() - m, (h - o["right"].height()) // 2)
        o["top"].move((w - o["top"].width()) // 2, m)
        o["bottom"].move((w - o["bottom"].width()) // 2, h - o["bottom"].height() - m)
        self.message.move((w - self.message.width()) // 2, (h - self.message.height()) // 2)
