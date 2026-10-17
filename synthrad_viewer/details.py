"""Side panel with the selected case's details: patient, scans, intensities, overview."""
from __future__ import annotations

from pathlib import Path

import numpy as np
import pyqtgraph as pg
from PySide6.QtCore import QObject, QRunnable, QSize, Qt, QThreadPool, Signal
from PySide6.QtGui import QGuiApplication, QImage, QImageReader, QPalette, QPixmap
from PySide6.QtWidgets import (QDialog, QFormLayout, QFrame, QLabel, QScrollArea,
                               QVBoxLayout, QWidget)

from .metadata import CaseInfo
from .volume import LoadedCase

THUMB_WIDTH = 900  # decoded size of the overview thumbnail (it is shown scaled down)


def _read_scaled(path: Path, width: int) -> QImage:
    reader = QImageReader(str(path))
    size = reader.size()
    if size.isValid() and size.width() > width:
        reader.setScaledSize(QSize(width, round(size.height() * width / size.width())))
    return reader.read()


class _ImageSignals(QObject):
    done = Signal(str, QImage)


class _ImageTask(QRunnable):
    def __init__(self, path: Path, signals: _ImageSignals):
        super().__init__()
        self.path, self.signals = path, signals

    def run(self):
        self.signals.done.emit(str(self.path), _read_scaled(self.path, THUMB_WIDTH))


class _ClickableImage(QLabel):
    clicked = Signal()

    def __init__(self):
        super().__init__()
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._pixmap: QPixmap | None = None

    def set_image(self, image: QImage | None):
        self._pixmap = QPixmap.fromImage(image) if image is not None else None
        self._rescale()

    def resizeEvent(self, ev):
        super().resizeEvent(ev)
        self._rescale()

    def _rescale(self):
        if self._pixmap is None or self._pixmap.isNull():
            self.clear()
            return
        w = max(50, self.width())
        scaled = self._pixmap.scaledToWidth(w * self.devicePixelRatioF(),
                                            Qt.TransformationMode.SmoothTransformation)
        scaled.setDevicePixelRatio(self.devicePixelRatioF())
        self.setPixmap(scaled)
        self.setMinimumHeight(1)
        self.setFixedHeight(round(scaled.height() / self.devicePixelRatioF()))

    def mouseReleaseEvent(self, ev):
        if ev.button() == Qt.MouseButton.LeftButton:
            self.clicked.emit()


class OverviewDialog(QDialog):
    def __init__(self, path: Path, title: str, parent=None):
        super().__init__(parent)
        self.setWindowTitle(title)
        screen = QGuiApplication.primaryScreen().availableGeometry()
        image = _read_scaled(path, min(2400, round(screen.width() * 0.85 * 2)))
        pixmap = QPixmap.fromImage(image)
        pixmap.setDevicePixelRatio(2.0)
        label = QLabel()
        label.setPixmap(pixmap)
        label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        scroll = QScrollArea()
        scroll.setWidget(label)
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(scroll)
        w = min(round(screen.width() * 0.85), round(pixmap.width() / 2) + 20)
        h = min(round(screen.height() * 0.85), round(pixmap.height() / 2) + 20)
        self.resize(w, h)


class _Histogram(pg.PlotWidget):
    """Value distribution inside the body with the current window as a draggable band."""
    windowDragged = Signal(float, float)

    def __init__(self):
        palette = QGuiApplication.palette()
        bg = palette.color(QPalette.ColorRole.Base)
        fg = palette.color(QPalette.ColorRole.Text)
        super().__init__(background=bg)
        self.setFixedHeight(120)
        self.setMenuEnabled(False)
        self.setMouseEnabled(x=False, y=False)
        self.hideButtons()
        self.hideAxis("left")
        axis = self.getAxis("bottom")
        axis.setPen(pg.mkPen(fg))
        axis.setTextPen(pg.mkPen(fg))
        self.curve = self.plot([0, 1], [0], stepMode="center", fillLevel=0,
                               pen=pg.mkPen(fg, width=1), brush=pg.mkBrush(fg.red(), fg.green(),
                                                                         fg.blue(), 70))
        self.region = pg.LinearRegionItem(brush=pg.mkBrush(255, 200, 61, 60),
                                          pen=pg.mkPen(255, 200, 61, 220, width=2))
        self.region.setZValue(10)
        self.addItem(self.region)
        self.region.sigRegionChanged.connect(self._dragged)
        self._setting = False

    def set_data(self, edges: np.ndarray, counts: np.ndarray) -> None:
        self.curve.setData(edges, np.sqrt(counts))  # sqrt keeps small peaks visible
        self.setXRange(float(edges[0]), float(edges[-1]), padding=0.02)
        self.setYRange(0, float(np.sqrt(counts).max() or 1), padding=0.05)

    def set_window(self, lo: float, hi: float) -> None:
        self._setting = True
        self.region.setRegion((lo, hi))
        self._setting = False

    def _dragged(self) -> None:
        if not self._setting:
            lo, hi = self.region.getRegion()
            self.windowDragged.emit(lo, hi)


def _section(text: str) -> QLabel:
    label = QLabel(text)
    label.setStyleSheet("font-weight: 600; font-size: 13px; margin-top: 14px;")
    return label


def _muted(text: str) -> QLabel:
    label = QLabel(text)
    label.setWordWrap(True)
    label.setStyleSheet("color: palette(placeholder-text);")
    return label


class DetailsPanel(QScrollArea):
    windowDragged = Signal(str, float, float)  # row ("source"/"ct"), lo, hi

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWidgetResizable(True)
        self.setFrameShape(QFrame.Shape.NoFrame)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setMinimumWidth(280)
        self.pool = QThreadPool(self)
        self.pool.setMaxThreadCount(1)
        self.image_signals = _ImageSignals()
        self.image_signals.done.connect(self._image_loaded)
        self._overview: Path | None = None
        self._case_id = ""

        body = QWidget()
        self.layout_ = QVBoxLayout(body)
        self.layout_.setContentsMargins(14, 10, 14, 14)
        self.layout_.setSpacing(4)
        self.heading = QLabel()
        self.heading.setStyleSheet("font-size: 18px; font-weight: 600;")
        self.subheading = _muted("")
        self.layout_.addWidget(self.heading)
        self.layout_.addWidget(self.subheading)

        self.info_box = QVBoxLayout()
        self.layout_.addLayout(self.info_box)

        self.layout_.addWidget(_section("Intensities inside the body"))
        self.hist = {}
        self.hist_titles = {}
        for row in ("source", "ct"):
            title = _muted("")
            hist = _Histogram()
            hist.windowDragged.connect(lambda lo, hi, r=row: self.windowDragged.emit(r, lo, hi))
            self.hist[row], self.hist_titles[row] = hist, title
            self.layout_.addWidget(title)
            self.layout_.addWidget(hist)
        self.layout_.addWidget(_muted("Drag the yellow band or its edges to change "
                                      "brightness and contrast."))

        self.layout_.addWidget(_section("Overview"))
        self.overview = _ClickableImage()
        self.overview.clicked.connect(self._open_overview)
        self.overview_note = _muted("")
        self.layout_.addWidget(self.overview)
        self.layout_.addWidget(self.overview_note)
        self.layout_.addStretch(1)
        self.setWidget(body)

    def _clear_info(self) -> None:
        def clear(layout):
            while layout.count():
                item = layout.takeAt(0)
                if item.widget():
                    item.widget().deleteLater()
                elif item.layout():
                    clear(item.layout())
        clear(self.info_box)

    def set_case(self, loaded: LoadedCase, info: CaseInfo | None) -> None:
        case = loaded.case
        self._case_id = case.case_id
        self.heading.setText(case.case_id)
        self.subheading.setText(f"Task {case.task}  ·  {case.region_name}  ·  "
                                f"{case.source_name} → CT")
        self._clear_info()
        if info is None or not (info.patient or info.scans):
            self.info_box.addWidget(_muted("No scan details were found for this case "
                                           "(they come from the overviews folder of the "
                                           "original download)."))
        else:
            for title, rows in [("Patient", info.patient), *info.scans.items()]:
                if not rows:
                    continue
                self.info_box.addWidget(_section(title))
                form = QFormLayout()
                form.setHorizontalSpacing(12)
                form.setVerticalSpacing(3)
                form.setLabelAlignment(Qt.AlignmentFlag.AlignLeft)
                form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow)
                for key, value in rows:
                    k = _muted(key)
                    k.setWordWrap(False)
                    v = QLabel(value)
                    v.setWordWrap(True)
                    v.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
                    form.addRow(k, v)
                self.info_box.addLayout(form)

        unit = "" if case.source == "mr" else " (HU)"
        self.hist_titles["source"].setText(f"{case.source_name}{unit}")
        self.hist_titles["ct"].setText("CT (HU)")
        for row in ("source", "ct"):
            self.hist[row].set_data(*loaded.hist[row])

        self._overview = info.overview if info else None
        self.overview.set_image(None)
        if self._overview is not None:
            self.overview_note.setText("Prepared by the challenge organisers. "
                                       "Click to enlarge.")
            self.pool.start(_ImageTask(self._overview, self.image_signals))
        else:
            self.overview_note.setText("No overview image for this case.")

    def set_window(self, row: str, lo: float, hi: float) -> None:
        self.hist[row].set_window(lo, hi)

    def _image_loaded(self, path: str, image: QImage) -> None:
        if self._overview is not None and path == str(self._overview):
            self.overview.set_image(image)

    def _open_overview(self) -> None:
        if self._overview is not None:
            QGuiApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
            try:
                dialog = OverviewDialog(self._overview, f"{self._case_id} — Overview", self)
            finally:
                QGuiApplication.restoreOverrideCursor()
            dialog.exec()
