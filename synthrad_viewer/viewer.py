"""The image grid: MRI/CBCT, CT and an optional comparison, in one plane or all three.

All panels share one 3-D cursor (``focus``, a voxel). Each plane shows the slice through
it; clicking moves it, scrolling moves it along that plane's axis. Panels showing the same
plane are linked for zoom and pan.
"""
from __future__ import annotations

import numpy as np
import pyqtgraph as pg
from PySide6.QtCore import QEvent, Qt, QTimer, Signal
from PySide6.QtWidgets import QGridLayout, QWidget

from .views import SlicePanel, mask_overlay
from .volume import (PLANE_AXIS, PLANES, LoadedCase, checkerboard, colour_overlay,
                     difference, normalise)

ROWS = ("source", "ct", "compare")
CHECKER_MM = 20.0
DIFF_LUT = pg.ColorMap(pos=[0.0, 0.5, 1.0],
                       color=[(50, 130, 255), (0, 0, 0), (255, 90, 40)]).getLookupTable(nPts=256)


def _fmt(value) -> str:
    text = f"{value:.0f}" if isinstance(value, (float, np.floating)) else str(value)
    return text.replace("-", "−")


class ViewerGrid(QWidget):
    hoverChanged = Signal(str)  # status-bar HTML
    activePlaneChanged = Signal(str)
    positionChanged = Signal()  # focus moved
    levelsChanged = Signal(str)  # "source" or "ct"

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setStyleSheet("background: #1a1a1a;")
        self.loaded: LoadedCase | None = None
        self.focus = [0, 0, 0]  # voxel (x, y, z)
        self.fraction = [0.5, 0.5, 0.5]  # focus relative to the volume, kept across cases
        self.active = "axial"
        self.layout_mode = "single"  # or "all"
        self.compare = "off"
        self.mask_mode = "off"
        self.crosshair = True
        self.levels = {"source": (0.0, 1.0), "ct": (0.0, 1.0), "difference": (-500.0, 500.0)}

        self.grid = QGridLayout(self)
        self.grid.setContentsMargins(0, 0, 0, 0)
        self.grid.setSpacing(2)
        self.panels = {(row, plane): SlicePanel(plane) for row in ROWS for plane in PLANES}
        for (row, plane), panel in self.panels.items():
            panel.hide()
            panel.hovered.connect(lambda xyz, p=panel: self._hovered(p, xyz))
            panel.clicked.connect(lambda xyz, pl=plane: self.set_focus(xyz, pl))
            panel.sliceScrolled.connect(lambda n, pl=plane: self.step(n, pl))
            panel.levelsEdited.connect(lambda lo, hi, r=row: self._levels_edited(r, lo, hi))
            panel.fitRequested.connect(self.fit)
            panel.view.installEventFilter(self)
            if row != "source":
                lead = self.panels[("source", plane)].vb
                panel.vb.setXLink(lead)
                panel.vb.setYLink(lead)
        self._arrange()
        for panel in self.panels.values():
            panel.set_message("Select a case")

    # -- layout --------------------------------------------------------------------------

    def rows(self) -> list[str]:
        return ["source", "ct"] + (["compare"] if self.compare != "off" else [])

    def planes(self) -> list[str]:
        return list(PLANES) if self.layout_mode == "all" else [self.active]

    def visible_panels(self) -> list[SlicePanel]:
        return [self.panels[(r, p)] for r in self.rows() for p in self.planes()]

    def _arrange(self) -> None:
        for panel in self.panels.values():
            self.grid.removeWidget(panel)
            panel.hide()
        for i in range(4):
            self.grid.setRowStretch(i, 0)
            self.grid.setColumnStretch(i, 0)
        rows, planes = self.rows(), self.planes()
        for r, row in enumerate(rows):
            for c, plane in enumerate(planes):
                panel = self.panels[(row, plane)]
                if self.layout_mode == "all":
                    self.grid.addWidget(panel, r, c)
                    self.grid.setRowStretch(r, 1)
                    self.grid.setColumnStretch(c, 1)
                else:
                    self.grid.addWidget(panel, 0, r)
                    self.grid.setColumnStretch(r, 1)
                panel.show()
        self.grid.setRowStretch(0, 1)
        self._mark_active()

    def _mark_active(self) -> None:
        for (row, plane), panel in self.panels.items():
            active = self.layout_mode == "all" and plane == self.active
            colour = "#ffc83d" if active else "rgba(255,255,255,0.82)"
            panel.slice_label.setStyleSheet(f"color: {colour}; background: transparent; "
                                            f"font-size: 12px;")

    def set_layout(self, mode: str) -> None:
        if mode == self.layout_mode:
            return
        self.layout_mode = mode
        self._relayout()

    def set_compare(self, mode: str) -> None:
        if mode == self.compare:
            return
        self.compare = mode
        self._relayout()

    def _relayout(self) -> None:
        self._arrange()
        self.render()
        QTimer.singleShot(0, self.fit)  # after the layout has sized the panels

    def set_active(self, plane: str) -> None:
        if plane == self.active:
            return
        self.active = plane
        if self.layout_mode == "single":
            self._relayout()
        else:
            self._mark_active()
        self.activePlaneChanged.emit(plane)
        self.positionChanged.emit()

    def set_mask_mode(self, mode: str) -> None:
        self.mask_mode = mode
        self.render()

    def set_crosshair(self, on: bool) -> None:
        self.crosshair = on
        self._update_crosshairs()

    # -- data ----------------------------------------------------------------------------

    def set_case(self, loaded: LoadedCase) -> None:
        refit = self.loaded is None or self.loaded.ct.data.shape != loaded.ct.data.shape
        self.loaded = loaded
        shape = loaded.ct.shape_xyz
        self.focus = [min(n - 1, max(0, round(f * (n - 1)))) for f, n in zip(self.fraction, shape)]
        self.render()
        if refit:
            QTimer.singleShot(0, self.fit)
        self.positionChanged.emit()

    def show_message(self, text: str) -> None:
        self.loaded = None
        for panel in self.panels.values():
            panel.set_message(text)

    def set_levels(self, row: str, lo: float, hi: float) -> None:
        self.levels[row] = (lo, hi)
        if self.loaded is None:
            return
        if row in ("source", "ct"):
            for plane in self.planes():
                self.panels[(row, plane)].set_levels(lo, hi)
            if self.compare == "checkerboard":
                for plane in self.planes():
                    self._render_panel("compare", plane)
            self.levelsChanged.emit(row)
        elif self.compare == "difference":
            for plane in self.planes():
                self.panels[("compare", plane)].set_levels(lo, hi)

    def _levels_edited(self, row: str, lo: float, hi: float) -> None:
        if row == "compare":
            if self.compare == "difference":
                r = max(1.0, (hi - lo) / 2)
                self.set_levels("difference", -r, r)
            return
        self.set_levels(row, lo, hi)

    # -- cursor --------------------------------------------------------------------------

    def n_slices(self, plane: str | None = None) -> int:
        if self.loaded is None:
            return 1
        return self.loaded.ct.shape_xyz[PLANE_AXIS[plane or self.active]]

    def index(self, plane: str | None = None) -> int:
        return self.focus[PLANE_AXIS[plane or self.active]]

    def set_index(self, index: int, plane: str | None = None) -> None:
        plane = plane or self.active
        if self.loaded is None:
            return
        axis = PLANE_AXIS[plane]
        index = max(0, min(self.n_slices(plane) - 1, index))
        if index == self.focus[axis]:
            return
        self.focus[axis] = index
        self._focus_moved([plane])

    def step(self, n: int, plane: str | None = None) -> None:
        plane = plane or self.active
        if plane != self.active:
            self.set_active(plane)
        self.set_index(self.index(plane) + n, plane)

    def set_focus(self, xyz, plane: str) -> None:
        """Click: move the cursor there; the clicked plane becomes the active one."""
        changed = [p for p in PLANES if self.focus[PLANE_AXIS[p]] != xyz[PLANE_AXIS[p]]]
        self.focus = list(xyz)
        if plane != self.active:
            self.set_active(plane)
        self._focus_moved(changed)

    def _focus_moved(self, planes) -> None:
        shape = self.loaded.ct.shape_xyz
        self.fraction = [f / max(1, n - 1) for f, n in zip(self.focus, shape)]
        visible = self.planes()
        self.render([p for p in planes if p in visible])
        for panel in self.visible_panels():
            panel.marker.hide()
        self.positionChanged.emit()

    # -- drawing -------------------------------------------------------------------------

    def render(self, planes=None) -> None:
        if self.loaded is None:
            return
        case = self.loaded.case
        titles = {"source": (case.source_name, ""), "ct": ("CT", ""),
                  "compare": self._compare_title()}
        for row in self.rows():
            for plane in (planes if planes is not None else self.planes()):
                self.panels[(row, plane)].set_title(*titles[row])
                self._render_panel(row, plane)
        self._update_crosshairs()

    def _compare_title(self) -> tuple[str, str]:
        """(title, legend) for the comparison panels."""
        name = self.loaded.case.source_name
        if self.compare == "difference":
            return (f"CT − {name}",
                    f"<span style='color: #ff7a4d'>■</span> CT higher&nbsp;&nbsp;&nbsp;"
                    f"<span style='color: #5a9bff'>■</span> {name} higher")
        if self.compare == "checkerboard":
            return "Checkerboard", f"{name} and CT in alternating squares"
        return ("Colour overlay",
                f"<span style='color: #6dff6d'>■</span> {name}&nbsp;&nbsp;&nbsp;"
                f"<span style='color: #ff6dff'>■</span> CT&nbsp;&nbsp;&nbsp;grey where they agree")

    def _render_panel(self, row: str, plane: str) -> None:
        lc, panel = self.loaded, self.panels[(row, plane)]
        idx = self.focus[PLANE_AXIS[plane]]
        mask = lc.mask.slice(plane, idx) if lc.mask is not None else None
        overlay = mask_overlay(mask, self.mask_mode) if mask is not None else None
        if row in ("source", "ct"):
            volume = lc.source if row == "source" else lc.ct
            panel.level_style = "window"
            panel.levels = self.levels[row]
            panel.set_content(volume, idx, volume.slice(plane, idx), overlay)
            panel.set_levels(*self.levels[row])
            return
        src, ct = lc.source.slice(plane, idx), lc.ct.slice(plane, idx)
        if self.compare == "difference":
            if lc.case.source == "mr":
                panel.set_message("Difference maps need CBCT:\nMRI values are not in HU")
                return
            panel.level_style = "symmetric"
            panel.levels = self.levels["difference"]
            panel.set_content(lc.ct, idx, difference(ct, src, mask), overlay, lut=DIFF_LUT)
            panel.set_levels(*self.levels["difference"])
            return
        if self.compare == "checkerboard":  # looks like the two panels above it
            a = normalise(src, self.levels["source"])
            b = normalise(ct, self.levels["ct"])
            du, dv = lc.ct.pixel_size(plane)
            image = checkerboard(a, b, (round(CHECKER_MM / dv), round(CHECKER_MM / du)))
        else:  # each scan scaled by its own automatic window, so their brightness matches
            image = colour_overlay(normalise(ct, lc.auto["ct"]), normalise(src, lc.auto["source"]))
        panel.level_style = "none"
        panel.levels = (0.0, 1.0)
        panel.set_content(lc.ct, idx, image, overlay)
        panel.set_levels(0.0, 1.0)

    def _update_crosshairs(self) -> None:
        show = self.crosshair and self.loaded is not None
        for panel in self.visible_panels():
            panel.set_crosshair(self.focus if show else None)

    def fit(self) -> None:
        if self.loaded is None:
            return
        for plane in self.planes():
            self.panels[("source", plane)].fit()

    # -- hover and keys ------------------------------------------------------------------

    def _hovered(self, sender: SlicePanel, xyz) -> None:
        for panel in self.visible_panels():
            panel.show_marker(xyz if panel is not sender else None)
        if xyz is None or self.loaded is None:
            self.hoverChanged.emit("")
            return
        lc = self.loaded
        name = lc.case.source_name
        parts = []
        if lc.source.contains(xyz):
            unit = "" if lc.case.source == "mr" else " HU"
            parts.append(f"<b>{name}</b> {_fmt(lc.source.value(xyz))}{unit}")
        if lc.ct.contains(xyz):
            parts.append(f"<b>CT</b> {_fmt(lc.ct.value(xyz))} HU")
        if lc.case.source == "cbct" and lc.source.contains(xyz):
            d = int(lc.ct.value(xyz)) - int(lc.source.value(xyz))
            parts.append(f"<b>CT − CBCT</b> {_fmt(d)} HU")
        if lc.mask is not None and lc.mask.contains(xyz):
            parts.append("inside body" if lc.mask.value(xyz) else "outside body")
        x, y, z = xyz
        parts.append(f"<span style='color: gray'>voxel {x}, {y}, {z}</span>")
        self.hoverChanged.emit("&nbsp;&nbsp;&nbsp;&nbsp;".join(parts))

    def eventFilter(self, obj, ev):
        # Arrow keys scroll the active plane while an image has focus.
        if ev.type() == QEvent.Type.KeyPress and self.loaded is not None:
            key = ev.key()
            step = 10 if ev.modifiers() & Qt.KeyboardModifier.ShiftModifier else 1
            if key in (Qt.Key.Key_Up, Qt.Key.Key_Right):
                self.step(step)
                return True
            if key in (Qt.Key.Key_Down, Qt.Key.Key_Left):
                self.step(-step)
                return True
            if key == Qt.Key.Key_Home:
                self.set_index(0)
                return True
            if key == Qt.Key.Key_End:
                self.set_index(self.n_slices() - 1)
                return True
        return super().eventFilter(obj, ev)
