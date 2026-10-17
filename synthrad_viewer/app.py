"""Main window: case browser on the left, linked MRI/CBCT and CT panels on the right."""
from __future__ import annotations

import sys
import traceback
from pathlib import Path

import pyqtgraph as pg
from PySide6.QtCore import (QModelIndex, QObject, QRunnable, QSettings, QSize,
                            QSortFilterProxyModel, Qt, QThreadPool, Signal)
from PySide6.QtGui import (QAction, QActionGroup, QFont, QKeySequence, QStandardItem,
                           QStandardItemModel)
from PySide6.QtWidgets import (QAbstractItemView, QApplication, QComboBox, QDockWidget,
                               QFileDialog, QHBoxLayout, QHeaderView, QLabel, QLineEdit,
                               QMainWindow, QMessageBox, QPushButton, QSizePolicy, QSlider,
                               QSplitter, QStackedWidget, QToolBar, QTreeView, QVBoxLayout,
                               QWidget)

from . import __version__
from .dataset import REGIONS, TASKS, Case, scan
from .details import DetailsPanel
from .icon import app_icon
from .metadata import MetadataIndex
from .viewer import ViewerGrid
from .volume import (AUTO, COMPARE_MODES, PLANES, PRESETS, CaseCache, LoadedCase, load_case,
                     preset_levels)

APP_NAME = "SynthRAD Viewer"
ROLE_CASE = Qt.ItemDataRole.UserRole + 1
MAX_RECENT = 6


# -- background loading ----------------------------------------------------------------

class _LoadSignals(QObject):
    finished = Signal(object, object, str)  # Case, LoadedCase | None, error


class _LoadTask(QRunnable):
    def __init__(self, case: Case, cache: CaseCache, metadata: MetadataIndex,
                 signals: _LoadSignals):
        super().__init__()
        self.case, self.cache, self.metadata, self.signals = case, cache, metadata, signals

    def run(self):
        try:
            loaded = self.cache.get(self.case.key)
            if loaded is None:
                loaded = load_case(self.case)
                try:
                    loaded.info = self.metadata.info(self.case)
                except Exception:  # details are optional; the images still open
                    loaded.info = None
            self.cache.put(self.case.key, loaded)
            self.signals.finished.emit(self.case, loaded, "")
        except Exception as e:  # unreadable or corrupt file
            self.signals.finished.emit(self.case, None, str(e) or type(e).__name__)


# -- case browser ----------------------------------------------------------------------

class _CaseFilter(QSortFilterProxyModel):
    """Shows a row if it, an ancestor, or (via recursive filtering) a descendant matches."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.text = ""
        self.setRecursiveFilteringEnabled(True)

    def set_text(self, text: str) -> None:
        self.text = text.strip().lower()
        self.invalidateFilter()

    def filterAcceptsRow(self, row, parent):
        if not self.text:
            return True
        index = self.sourceModel().index(row, 0, parent)
        while index.isValid():
            if self.text in (index.data() or "").lower():
                return True
            index = index.parent()
        return False


def _build_model(cases: list[Case]) -> QStandardItemModel:
    model = QStandardItemModel()
    bold = QFont()
    bold.setBold(True)
    groups: dict[int, dict[str, list[Case]]] = {}
    for case in cases:
        groups.setdefault(case.task, {}).setdefault(case.region, []).append(case)
    for task, regions in groups.items():
        task_item = QStandardItem(f"Task {task}  ·  {TASKS[task]}")
        task_item.setFont(bold)
        task_item.setSelectable(False)
        count = QStandardItem(str(sum(len(c) for c in regions.values())))
        count.setSelectable(False)
        for region, region_cases in regions.items():
            region_item = QStandardItem(REGIONS.get(region, "Other"))
            region_item.setSelectable(False)
            region_count = QStandardItem(str(len(region_cases)))
            region_count.setSelectable(False)
            for case in region_cases:
                item = QStandardItem(case.case_id)
                item.setData(case, ROLE_CASE)
                item.setToolTip(str(case.path))
                region_item.appendRow([item, QStandardItem("")])
            task_item.appendRow([region_item, region_count])
        model.appendRow([task_item, count])
    for item in _walk_items(model):
        item.setEditable(False)
    return model


def _walk_items(model: QStandardItemModel):
    stack = [model.invisibleRootItem()]
    while stack:
        parent = stack.pop()
        for r in range(parent.rowCount()):
            for c in range(parent.columnCount()):
                child = parent.child(r, c)
                if child is not None:
                    if c == 1:
                        child.setForeground(Qt.GlobalColor.gray)
                        child.setTextAlignment(Qt.AlignmentFlag.AlignRight
                                               | Qt.AlignmentFlag.AlignVCenter)
                    yield child
                    stack.append(child)


# -- welcome page ----------------------------------------------------------------------

class WelcomePage(QWidget):
    openRequested = Signal()
    recentRequested = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        outer = QVBoxLayout(self)
        outer.addStretch(2)
        column = QVBoxLayout()
        column.setSpacing(10)
        icon = QLabel()
        icon.setPixmap(app_icon().pixmap(QSize(112, 112)))
        icon.setAlignment(Qt.AlignmentFlag.AlignCenter)
        title = QLabel(APP_NAME)
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        title.setStyleSheet("font-size: 26px; font-weight: 600;")
        subtitle = QLabel("Browse the CT, MRI and CBCT scans of the SynthRAD2025 challenge.")
        subtitle.setAlignment(Qt.AlignmentFlag.AlignCenter)
        subtitle.setStyleSheet("font-size: 14px; color: palette(placeholder-text);")
        self.button = QPushButton("Open Dataset Folder…")
        self.button.setDefault(True)
        self.button.setMinimumWidth(220)
        self.button.setMinimumHeight(34)
        self.button.clicked.connect(self.openRequested)
        hint = QLabel("Pick the folder you unzipped the dataset into. "
                      "The cases inside it are found automatically.")
        hint.setAlignment(Qt.AlignmentFlag.AlignCenter)
        hint.setWordWrap(True)
        hint.setStyleSheet("color: palette(placeholder-text);")
        for w in (icon, title, subtitle):
            column.addWidget(w)
        column.addSpacing(14)
        column.addWidget(self.button, alignment=Qt.AlignmentFlag.AlignCenter)
        column.addWidget(hint)
        self.recent_title = QLabel("Recent")
        self.recent_title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.recent_title.setStyleSheet("font-weight: 600; margin-top: 18px;")
        column.addWidget(self.recent_title)
        self.recent_box = QVBoxLayout()
        self.recent_box.setSpacing(2)
        column.addLayout(self.recent_box)
        row = QHBoxLayout()
        row.addStretch()
        holder = QWidget()
        holder.setLayout(column)
        holder.setMaximumWidth(520)
        row.addWidget(holder)
        row.addStretch()
        outer.addLayout(row)
        outer.addStretch(3)

    def set_recent(self, folders: list[str]) -> None:
        while self.recent_box.count():
            self.recent_box.takeAt(0).widget().deleteLater()
        for folder in folders:
            b = QPushButton(Path(folder).name)
            b.setFlat(True)
            b.setToolTip(folder)
            b.setCursor(Qt.CursorShape.PointingHandCursor)
            b.setStyleSheet("QPushButton { color: palette(link); border: none; padding: 2px; }"
                            "QPushButton:hover { text-decoration: underline; }")
            b.clicked.connect(lambda _=False, f=folder: self.recentRequested.emit(f))
            self.recent_box.addWidget(b, alignment=Qt.AlignmentFlag.AlignCenter)
        self.recent_title.setVisible(bool(folders))


# -- main window -----------------------------------------------------------------------

MASK_MODES = {"off": "Off", "outline": "Outline", "filled": "Filled", "dim": "Dim outside"}


def _setting(settings: QSettings, key: str, default: str, allowed) -> str:
    value = settings.value(key, default)
    return value if value in allowed else default


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.settings = QSettings()
        self.cache = CaseCache()
        self.metadata = MetadataIndex()
        self.pool = QThreadPool(self)
        self.pool.setMaxThreadCount(2)
        self.signals = _LoadSignals()
        self.signals.finished.connect(self._loaded)
        self.in_flight: set[str] = set()

        self.root: str | None = None
        self.cases: list[Case] = []
        self.wanted: Case | None = None
        self.current: LoadedCase | None = None
        self.preset = _setting(self.settings, "preset", "Soft tissue", [*PRESETS, AUTO])

        self.setWindowTitle(APP_NAME)
        self.setUnifiedTitleAndToolBarOnMac(True)
        self._build_ui()
        self._build_actions()
        self._build_menus()
        self._build_toolbar()

        s = self.settings
        self.grid.active = _setting(s, "plane", "axial", PLANES)
        self.set_layout(_setting(s, "layout", "single", ("single", "all")))
        self.set_compare(_setting(s, "compare", "off", COMPARE_MODES))
        self.set_mask_mode(_setting(s, "mask", "off", MASK_MODES))
        self.set_crosshair(s.value("crosshair", "true") in (True, "true"))
        self._plane_changed(self.grid.active)

        geometry = s.value("geometry")
        if geometry is None or not self.restoreGeometry(geometry):
            self.resize(1440, 860)
        state = s.value("window_state")
        if state is not None:
            self.restoreState(state)
        self._update_enabled()
        root = s.value("root")
        if root and Path(root).is_dir():
            self.open_folder(root, restore_case=s.value("case"))
        else:
            self._show_welcome()

    # -- construction ------------------------------------------------------------------

    def _build_ui(self):
        self.stack = QStackedWidget()
        self.stack.setAutoFillBackground(True)
        self.setCentralWidget(self.stack)
        self.welcome = WelcomePage()
        self.welcome.openRequested.connect(self.choose_folder)
        self.welcome.recentRequested.connect(self.open_folder)
        self.stack.addWidget(self.welcome)

        # sidebar
        sidebar = QWidget()
        side = QVBoxLayout(sidebar)
        side.setContentsMargins(8, 8, 0, 8)
        side.setSpacing(6)
        self.search = QLineEdit()
        self.search.setPlaceholderText("Search cases")
        self.search.setClearButtonEnabled(True)
        self.search.textChanged.connect(self._filter)
        self.model = QStandardItemModel()
        self.proxy = _CaseFilter(self)
        self.proxy.setSourceModel(self.model)
        self.tree = QTreeView()
        self.tree.setModel(self.proxy)
        self.tree.setHeaderHidden(True)
        self.tree.setUniformRowHeights(True)
        self.tree.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.tree.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.tree.setAttribute(Qt.WidgetAttribute.WA_MacShowFocusRect, False)
        self.tree.selectionModel().currentChanged.connect(self._tree_current_changed)
        self.dataset_label = QLabel()
        self.dataset_label.setStyleSheet("color: palette(placeholder-text);")
        self.dataset_label.setWordWrap(True)
        side.addWidget(self.search)
        side.addWidget(self.tree, 1)
        side.addWidget(self.dataset_label)

        # viewer
        viewer = QWidget()
        v = QVBoxLayout(viewer)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(0)
        self.grid = ViewerGrid()
        self.grid.hoverChanged.connect(lambda html: self.hover_label.setText(html))
        self.grid.positionChanged.connect(self._sync_slider)
        self.grid.activePlaneChanged.connect(self._plane_changed)
        self.grid.levelsChanged.connect(
            lambda row: self.details.set_window(row, *self.grid.levels[row]))
        v.addWidget(self.grid, 1)

        bar = QWidget()
        b = QHBoxLayout(bar)
        b.setContentsMargins(14, 8, 14, 8)
        self.slider = QSlider(Qt.Orientation.Horizontal)
        self.slider.valueChanged.connect(lambda i: self.grid.set_index(i))
        self.slider_plane = QLabel("Axial")
        self.slider_plane.setMinimumWidth(60)
        self.slider_label = QLabel()
        self.slider_label.setMinimumWidth(90)
        self.slider_label.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        b.addWidget(self.slider_plane)
        b.addWidget(self.slider, 1)
        b.addWidget(self.slider_label)
        v.addWidget(bar)

        self.splitter = QSplitter()
        self.splitter.addWidget(sidebar)
        self.splitter.addWidget(viewer)
        self.splitter.setStretchFactor(1, 1)
        self.splitter.setSizes([230, 1100])
        state = self.settings.value("splitter")
        if state is not None:
            self.splitter.restoreState(state)
        self.stack.addWidget(self.splitter)

        self.details = DetailsPanel()
        self.details.windowDragged.connect(lambda row, lo, hi: self.grid.set_levels(row, lo, hi))
        self.dock = QDockWidget("Case Details", self)
        self.dock.setObjectName("details")
        self.dock.setWidget(self.details)
        self.dock.setFeatures(QDockWidget.DockWidgetFeature.DockWidgetClosable
                              | QDockWidget.DockWidgetFeature.DockWidgetMovable)
        self.dock.setAllowedAreas(Qt.DockWidgetArea.LeftDockWidgetArea
                                  | Qt.DockWidgetArea.RightDockWidgetArea)
        self.addDockWidget(Qt.DockWidgetArea.RightDockWidgetArea, self.dock)
        self.resizeDocks([self.dock], [320], Qt.Orientation.Horizontal)

        self.hover_label = QLabel()
        self.case_label = QLabel()
        self.case_label.setStyleSheet("color: palette(placeholder-text);")
        self.statusBar().addWidget(self.hover_label, 1)
        self.statusBar().addPermanentWidget(self.case_label)
        self.statusBar().setSizeGripEnabled(False)

    def _radio_actions(self, items: dict, current, slot, shortcuts=None):
        group = QActionGroup(self)
        actions = {}
        for key, text in items.items():
            a = QAction(text, self, checkable=True)
            if shortcuts and key in shortcuts:
                a.setShortcut(QKeySequence(shortcuts[key]))
            a.setChecked(key == current)
            a.triggered.connect(lambda _=False, k=key: slot(k))
            group.addAction(a)
            actions[key] = a
        return actions

    def _build_actions(self):
        self.act_open = QAction("Open Dataset Folder…", self, shortcut=QKeySequence.StandardKey.Open,
                                triggered=self.choose_folder)
        self.act_prev = QAction("Previous Case", self, triggered=lambda: self.step_case(-1))
        self.act_prev.setShortcuts([QKeySequence("Ctrl+["), QKeySequence(Qt.Key.Key_PageUp)])
        self.act_next = QAction("Next Case", self, triggered=lambda: self.step_case(1))
        self.act_next.setShortcuts([QKeySequence("Ctrl+]"), QKeySequence(Qt.Key.Key_PageDown)])
        self.act_fit = QAction("Fit to Window", self, shortcut=QKeySequence("Ctrl+0"),
                               triggered=self.grid.fit)
        self.act_reset_window = QAction("Reset Brightness/Contrast", self,
                                        shortcut=QKeySequence("Ctrl+R"),
                                        triggered=self.apply_window)
        self.act_find = QAction("Search Cases", self, shortcut=QKeySequence.StandardKey.Find,
                                triggered=lambda: (self.search.setFocus(), self.search.selectAll()))
        self.act_all_planes = QAction("All Three Planes", self, checkable=True,
                                      shortcut=QKeySequence("Ctrl+4"))
        self.act_all_planes.triggered.connect(
            lambda on: self.set_layout("all" if on else "single"))
        self.act_crosshair = QAction("Show Crosshair", self, checkable=True,
                                     shortcut=QKeySequence("Ctrl+Shift+C"))
        self.act_crosshair.triggered.connect(self.set_crosshair)

        self.plane_actions = self._radio_actions(
            {p: p.capitalize() for p in PLANES}, self.grid.active, self.set_plane,
            {p: f"Ctrl+{i}" for i, p in enumerate(PLANES, start=1)})
        self.preset_actions = self._radio_actions(
            {name: name for name in [*PRESETS, AUTO]}, self.preset, self.set_preset)
        self.compare_actions = self._radio_actions(COMPARE_MODES, "off", self.set_compare)
        self.mask_actions = self._radio_actions(MASK_MODES, "off", self.set_mask_mode)

        self.act_details = self.dock.toggleViewAction()
        self.act_details.setText("Case Details")
        self.act_details.setShortcut(QKeySequence("Ctrl+I"))

    def _build_menus(self):
        m = self.menuBar()
        file = m.addMenu("File")
        file.addAction(self.act_open)
        self.recent_menu = file.addMenu("Open Recent")
        self._refresh_recent()

        view = m.addMenu("View")
        for a in self.plane_actions.values():
            view.addAction(a)
        view.addAction(self.act_all_planes)
        view.addSeparator()
        compare = view.addMenu("Compare")
        for a in self.compare_actions.values():
            compare.addAction(a)
        mask = view.addMenu("Body Mask")
        for a in self.mask_actions.values():
            mask.addAction(a)
        view.addAction(self.act_crosshair)
        view.addSeparator()
        window = view.addMenu("CT Window")
        for a in self.preset_actions.values():
            window.addAction(a)
        view.addAction(self.act_reset_window)
        view.addSeparator()
        view.addAction(self.act_fit)
        view.addAction(self.act_details)

        go = m.addMenu("Go")
        go.addAction(self.act_prev)
        go.addAction(self.act_next)
        go.addSeparator()
        go.addAction(self.act_find)

        help_menu = m.addMenu("Help")
        help_menu.addAction(QAction("Mouse and Keyboard Controls", self,
                                    shortcut=QKeySequence("Ctrl+/"), triggered=self.show_controls))
        help_menu.addAction(QAction(f"About {APP_NAME}", self, triggered=self.show_about,
                                    menuRole=QAction.MenuRole.AboutRole))

    def _combo(self, tb: QToolBar, label: str, items: dict, slot, tooltip: str) -> QComboBox:
        tb.addWidget(QLabel(f"  {label}  "))
        combo = QComboBox()
        for key, text in items.items():
            combo.addItem(text, key)
        combo.setToolTip(tooltip)
        combo.activated.connect(lambda i, c=combo: slot(c.itemData(i)))
        tb.addWidget(combo)
        return combo

    def _build_toolbar(self):
        tb = QToolBar("Toolbar")
        tb.setObjectName("toolbar")
        tb.setMovable(False)
        tb.setFloatable(False)
        tb.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextOnly)
        tb.setContextMenuPolicy(Qt.ContextMenuPolicy.PreventContextMenu)
        self.addToolBar(tb)
        prev_button = QAction("‹  Previous", self, triggered=lambda: self.step_case(-1))
        next_button = QAction("Next  ›", self, triggered=lambda: self.step_case(1))
        prev_button.setToolTip("Previous case  (Page Up)")
        next_button.setToolTip("Next case  (Page Down)")
        self.case_nav = [prev_button, next_button]
        tb.addAction(prev_button)
        tb.addAction(next_button)
        tb.addSeparator()
        native = QKeySequence.SequenceFormat.NativeText
        for a in self.plane_actions.values():
            a.setToolTip(f"{a.text()} view  ({a.shortcut().toString(native)})")
            tb.addAction(a)
        all_button = QAction("All Planes", self, checkable=True)
        all_button.setToolTip("Show axial, coronal and sagittal together  (⌘4)")
        all_button.triggered.connect(lambda on: self.set_layout("all" if on else "single"))
        self.all_button = all_button
        tb.addAction(all_button)
        tb.addSeparator()
        self.preset_combo = self._combo(
            tb, "CT window", {n: n for n in [*PRESETS, AUTO]}, self.set_preset,
            "Brightness and contrast for CT. MRI and CBCT are adjusted automatically per "
            "image.\nRight-drag on any image to fine-tune.")
        self.compare_combo = self._combo(
            tb, "Compare", COMPARE_MODES, self.set_compare,
            "Adds a panel that compares the two scans:\n"
            "Difference: CT minus CBCT in HU (CBCT cases only)\n"
            "Checkerboard: alternating squares of both scans\n"
            "Colour overlay: MRI/CBCT in green, CT in magenta; grey where they agree")
        self.mask_combo = self._combo(
            tb, "Body mask", MASK_MODES, self.set_mask_mode,
            "Show the body outline the challenge scores inside")
        spacer = QWidget()
        spacer.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        tb.addWidget(spacer)
        fit = QAction("Fit", self, triggered=self.grid.fit)
        fit.setToolTip("Fit images to window  (⌘0 or double-click)")
        tb.addAction(fit)
        details = QAction("Details", self, checkable=True)
        details.setToolTip("Show or hide case details  (⌘I)")
        details.triggered.connect(self.dock.setVisible)
        self.dock.visibilityChanged.connect(
            lambda _: details.setChecked(not self.dock.isHidden()))
        self.details_button = details
        tb.addAction(details)
        self.toolbar = tb
        self.preset_combo.setCurrentIndex(self.preset_combo.findData(self.preset))

    # -- view settings -----------------------------------------------------------------

    def set_plane(self, plane: str):
        self.grid.set_active(plane)
        self._plane_changed(plane)

    def _plane_changed(self, plane: str):
        self.plane_actions[plane].setChecked(True)
        self.slider_plane.setText(plane.capitalize())
        self.settings.setValue("plane", plane)
        self._sync_slider()

    def set_layout(self, mode: str):
        self.grid.set_layout(mode)
        self.act_all_planes.setChecked(mode == "all")
        self.all_button.setChecked(mode == "all")
        self.settings.setValue("layout", mode)

    def set_compare(self, mode: str):
        self.grid.set_compare(mode)
        self.compare_actions[mode].setChecked(True)
        self.compare_combo.setCurrentIndex(self.compare_combo.findData(mode))
        self.settings.setValue("compare", mode)

    def set_mask_mode(self, mode: str):
        self.grid.set_mask_mode(mode)
        self.mask_actions[mode].setChecked(True)
        self.mask_combo.setCurrentIndex(self.mask_combo.findData(mode))
        self.settings.setValue("mask", mode)

    def set_crosshair(self, on: bool):
        self.grid.set_crosshair(on)
        self.act_crosshair.setChecked(on)
        self.settings.setValue("crosshair", "true" if on else "false")

    def set_preset(self, name: str):
        self.preset = name
        self.settings.setValue("preset", name)
        self.preset_actions[name].setChecked(True)
        self.preset_combo.setCurrentIndex(self.preset_combo.findData(name))
        self.apply_window()

    def apply_window(self):
        if self.current is None:
            return
        # MRI and CBCT values are not calibrated HU (center D's CBCT sits hundreds of HU
        # below its CT), so they are always windowed per image.
        self.grid.set_levels("source", *self.current.auto["source"])
        ct = self.current.auto["ct"] if self.preset == AUTO else preset_levels(self.preset)
        self.grid.set_levels("ct", *ct)

    def _sync_slider(self):
        n = self.grid.n_slices()
        index = self.grid.index()
        self.slider.blockSignals(True)
        self.slider.setRange(0, n - 1)
        self.slider.setValue(index)
        self.slider.setPageStep(max(1, n // 10))
        self.slider.blockSignals(False)
        self.slider_label.setText(f"{index + 1} of {n}" if self.current else "")

    # -- dataset -----------------------------------------------------------------------

    def choose_folder(self):
        start = self.root or self.settings.value("root") or str(Path.home())
        folder = QFileDialog.getExistingDirectory(self, "Open Dataset Folder", start)
        if folder:
            self.open_folder(folder)

    def open_folder(self, folder: str, restore_case: str | None = None):
        QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
        try:
            cases = scan(folder)
        finally:
            QApplication.restoreOverrideCursor()
        if not cases:
            self._forget_recent(folder)
            QMessageBox.information(
                self, "No Cases Found",
                f"No SynthRAD cases were found in “{Path(folder).name}”.\n\n"
                "Choose the folder that contains the unzipped Task 1 or Task 2 data. "
                "Each case folder holds ct.mha together with mr.mha or cbct.mha.")
            if self.root is None:
                self._show_welcome()
            return
        self.root, self.cases = folder, cases
        self.settings.setValue("root", folder)
        self._remember_recent(folder)
        self.model = _build_model(cases)
        self.proxy.setSourceModel(self.model)
        self.tree.header().setStretchLastSection(False)
        self.tree.header().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self.tree.header().setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        self.dataset_label.setText(f"{len(cases)} cases  ·  {Path(folder).name}")
        self.dataset_label.setToolTip(folder)
        self.setWindowFilePath(folder)
        self.stack.setCurrentWidget(self.splitter)
        self.current = self.wanted = None
        target = self._find_index(restore_case) if restore_case else None
        if target is None or not target.isValid():
            target = self._leaf_indices()[0]
        self._select(target)
        self._update_enabled()
        self.tree.setFocus()

    def _show_welcome(self):
        self.welcome.set_recent(self._recent())
        self.stack.setCurrentWidget(self.welcome)
        self._update_enabled()

    def _recent(self) -> list[str]:
        value = self.settings.value("recent", [])
        if isinstance(value, str):
            value = [value]
        return [f for f in (value or []) if Path(f).is_dir()]

    def _remember_recent(self, folder: str):
        recent = [folder] + [f for f in self._recent() if f != folder]
        self.settings.setValue("recent", recent[:MAX_RECENT])
        self._refresh_recent()

    def _forget_recent(self, folder: str):
        self.settings.setValue("recent", [f for f in self._recent() if f != folder])
        self._refresh_recent()

    def _refresh_recent(self):
        self.recent_menu.clear()
        recent = self._recent()
        for folder in recent:
            a = self.recent_menu.addAction(Path(folder).name)
            a.setToolTip(folder)
            a.triggered.connect(lambda _=False, f=folder: self.open_folder(f))
        if recent:
            self.recent_menu.addSeparator()
            self.recent_menu.addAction("Clear Menu", lambda: (
                self.settings.setValue("recent", []), self._refresh_recent()))
        self.recent_menu.setEnabled(bool(recent))

    def _filter(self, text: str):
        self.proxy.set_text(text)
        if text:
            self.tree.expandAll()
        elif self.wanted is not None:
            index = self._find_index(self.wanted.key)
            if index is not None:
                self._select(index)

    # -- tree navigation ---------------------------------------------------------------

    def _leaf_indices(self) -> list[QModelIndex]:
        out = []

        def walk(parent):
            for r in range(self.proxy.rowCount(parent)):
                index = self.proxy.index(r, 0, parent)
                if index.data(ROLE_CASE) is not None:
                    out.append(index)
                else:
                    walk(index)

        walk(QModelIndex())
        return out

    def _find_index(self, key: str) -> QModelIndex | None:
        for index in self._leaf_indices():
            if index.data(ROLE_CASE).key == key:
                return index
        return None

    def _select(self, index: QModelIndex):
        parent = index.parent()
        while parent.isValid():
            self.tree.expand(parent)
            parent = parent.parent()
        self.tree.setCurrentIndex(index)
        self.tree.scrollTo(index)

    def _tree_current_changed(self, current: QModelIndex, _previous=None):
        case = current.data(ROLE_CASE) if current.isValid() else None
        if case is not None:
            self.request_case(case)

    def step_case(self, step: int):
        leaves = self._leaf_indices()
        if not leaves:
            return
        keys = [i.data(ROLE_CASE).key for i in leaves]
        current = self.wanted.key if self.wanted else None
        pos = keys.index(current) + step if current in keys else 0
        self._select(leaves[max(0, min(len(leaves) - 1, pos))])

    # -- loading -----------------------------------------------------------------------

    def request_case(self, case: Case):
        if self.wanted is not None and self.wanted.key == case.key:
            return
        self.wanted = case
        self.settings.setValue("case", case.key)
        self._update_enabled()
        loaded = self.cache.get(case.key)
        if loaded is not None:
            self._show_case(loaded)
            return
        self.grid.show_message("Loading…")
        self.current = None
        self.case_label.setText(case.case_id)
        self._start_load(case)

    def _start_load(self, case: Case):
        if case.key in self.in_flight or self.cache.get(case.key) is not None:
            return
        self.in_flight.add(case.key)
        self.pool.start(_LoadTask(case, self.cache, self.metadata, self.signals))

    def _loaded(self, case: Case, loaded: LoadedCase | None, error: str):
        self.in_flight.discard(case.key)
        if self.wanted is None or case.key != self.wanted.key:
            return
        if loaded is None:
            self.current = None
            self.grid.show_message("This case could not be opened")
            self.hover_label.setText(f"{case.case_id}: {error}")
            return
        self._show_case(loaded)

    def _prefetch(self):
        keys = [i.data(ROLE_CASE) for i in self._leaf_indices()]
        ids = [c.key for c in keys]
        if self.wanted is None or self.wanted.key not in ids:
            return
        pos = ids.index(self.wanted.key)
        for p in (pos + 1, pos - 1):
            if 0 <= p < len(keys):
                self._start_load(keys[p])

    def _show_case(self, loaded: LoadedCase):
        self.current = loaded
        case = loaded.case
        self.setWindowTitle(f"{case.case_id} — {APP_NAME}")
        x, y, z = loaded.ct.shape_xyz
        sx, sy, sz = loaded.ct.spacing
        self.case_label.setText(f"{case.case_id}  ·  {case.region_name}  ·  {x} × {y} × {z}"
                                f"  ·  {sx:g} × {sy:g} × {sz:g} mm")
        self.hover_label.clear()
        self.details.set_case(loaded, loaded.info)
        self.apply_window_levels(loaded)  # levels first (no drawing), then the images
        self.grid.set_case(loaded)
        for row in ("source", "ct"):
            self.details.set_window(row, *self.grid.levels[row])
        self._prefetch()

    def apply_window_levels(self, loaded: LoadedCase):
        self.grid.levels["source"] = loaded.auto["source"]
        self.grid.levels["ct"] = (loaded.auto["ct"] if self.preset == AUTO
                                  else preset_levels(self.preset))

    # -- misc --------------------------------------------------------------------------

    def _update_enabled(self):
        has_data = self.stack.currentWidget() is self.splitter and bool(self.cases)
        for a in (self.act_prev, self.act_next, self.act_fit, self.act_reset_window,
                  self.act_find, self.act_all_planes, self.act_crosshair, self.act_details,
                  *self.case_nav, *self.plane_actions.values(), *self.compare_actions.values(),
                  *self.mask_actions.values()):
            a.setEnabled(has_data)
        self.toolbar.setVisible(has_data)
        self.statusBar().setVisible(has_data)
        if not has_data:
            self.dock.hide()
        elif self.settings.value("details_visible", "true") in (True, "true"):
            self.dock.show()

    def show_controls(self):
        rows = [
            ("Scroll", "Move through slices"),
            ("Click", "Move the crosshair there; the other planes follow"),
            ("↑ ↓  (click an image first)", "Previous / next slice — hold ⇧ for steps of 10"),
            ("Home / End", "First / last slice"),
            ("Pinch, or ⌘ + scroll", "Zoom"),
            ("Drag", "Move the image"),
            ("Right-drag", "Brightness (up/down) and contrast (left/right)"),
            ("Double-click, ⌘0", "Fit images to window"),
            ("⌘R", "Reset brightness and contrast"),
            ("⌘1  ⌘2  ⌘3", "Axial, coronal, sagittal view"),
            ("⌘4", "All three planes at once"),
            ("⇧⌘C", "Show or hide the crosshair"),
            ("⌘I", "Show or hide case details"),
            ("Page Up / Page Down,  ⌘[  ⌘]", "Previous / next case"),
            ("↑ ↓  in the case list", "Previous / next case"),
            ("⌘F", "Search cases"),
        ]
        html = "<table cellspacing='0' cellpadding='4'>" + "".join(
            f"<tr><td><b>{k}</b></td><td>&nbsp;&nbsp;{v}</td></tr>" for k, v in rows) + "</table>"
        box = QMessageBox(self)
        box.setWindowTitle("Mouse and Keyboard Controls")
        box.setText("<b>Mouse and Keyboard Controls</b>")
        box.setInformativeText(html)
        box.setIconPixmap(app_icon().pixmap(QSize(64, 64)))
        box.exec()

    def show_about(self):
        box = QMessageBox(self)
        box.setWindowTitle(f"About {APP_NAME}")
        box.setText(f"<b>{APP_NAME}</b> {__version__}")
        box.setInformativeText("A viewer for the SynthRAD2025 synthetic CT challenge data: "
                               "MRI → CT (Task 1) and CBCT → CT (Task 2).")
        box.setIconPixmap(app_icon().pixmap(QSize(64, 64)))
        box.exec()

    def closeEvent(self, ev):
        s = self.settings
        s.setValue("geometry", self.saveGeometry())
        s.setValue("window_state", self.saveState())
        s.setValue("splitter", self.splitter.saveState())
        if self.stack.currentWidget() is self.splitter:
            s.setValue("details_visible", "false" if self.dock.isHidden() else "true")
        super().closeEvent(ev)


def _set_macos_app_name(name: str) -> None:
    """Show the real name in the macOS menu bar instead of “Python”."""
    if sys.platform != "darwin":
        return
    try:
        from Foundation import NSBundle

        info = NSBundle.mainBundle().localizedInfoDictionary() or NSBundle.mainBundle().infoDictionary()
        info["CFBundleName"] = name
    except Exception:
        pass


def _excepthook(exc_type, exc, tb):
    traceback.print_exception(exc_type, exc, tb)
    app = QApplication.instance()
    if app is not None:
        QMessageBox.warning(app.activeWindow(), "Something Went Wrong",
                            f"{exc_type.__name__}: {exc}")


def main() -> int:
    _set_macos_app_name(APP_NAME)
    pg.setConfigOptions(imageAxisOrder="row-major", antialias=True)
    app = QApplication(sys.argv)
    app.setApplicationName(APP_NAME)
    app.setApplicationDisplayName(APP_NAME)
    app.setOrganizationName("SynthRAD")
    app.setWindowIcon(app_icon())
    sys.excepthook = _excepthook
    window = MainWindow()
    window.show()
    return app.exec()
