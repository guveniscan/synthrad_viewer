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
from PySide6.QtWidgets import (QAbstractItemView, QApplication, QComboBox, QFileDialog,
                               QHBoxLayout, QHeaderView, QLabel, QLineEdit, QMainWindow,
                               QMessageBox, QPushButton, QSizePolicy, QSlider, QSplitter,
                               QStackedWidget, QStyle, QToolBar, QTreeView, QVBoxLayout,
                               QWidget)

from . import __version__
from .dataset import REGIONS, TASKS, Case, scan
from .icon import app_icon
from .volume import AUTO, PLANES, PRESETS, CaseCache, LoadedCase, load_case, preset_levels
from .views import SlicePanel

APP_NAME = "SynthRAD Viewer"
ROLE_CASE = Qt.ItemDataRole.UserRole + 1
MAX_RECENT = 6


def _fmt(value) -> str:
    text = f"{value:.0f}" if isinstance(value, float) else str(value)
    return text.replace("-", "−")


# -- background loading ----------------------------------------------------------------

class _LoadSignals(QObject):
    finished = Signal(object, object, str)  # Case, LoadedCase | None, error


class _LoadTask(QRunnable):
    def __init__(self, case: Case, cache: CaseCache, signals: _LoadSignals):
        super().__init__()
        self.case, self.cache, self.signals = case, cache, signals

    def run(self):
        try:
            loaded = self.cache.get(self.case.key) or load_case(self.case)
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

class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.settings = QSettings()
        self.cache = CaseCache()
        self.pool = QThreadPool(self)
        self.pool.setMaxThreadCount(2)
        self.signals = _LoadSignals()
        self.signals.finished.connect(self._loaded)
        self.in_flight: set[str] = set()

        self.root: str | None = None
        self.cases: list[Case] = []
        self.wanted: Case | None = None
        self.current: LoadedCase | None = None
        self.plane = self.settings.value("plane", "axial")
        if self.plane not in PLANES:
            self.plane = "axial"
        self.index = 0
        self.position = {p: 0.5 for p in PLANES}  # relative slice position per plane
        self.preset = self.settings.value("preset", "Soft tissue")
        if self.preset not in PRESETS and self.preset != AUTO:
            self.preset = "Soft tissue"

        self.setWindowTitle(APP_NAME)
        self.setUnifiedTitleAndToolBarOnMac(True)
        self._build_ui()
        self._build_actions()
        self._build_menus()
        self._build_toolbar()
        self._update_enabled()

        geometry = self.settings.value("geometry")
        if geometry is None or not self.restoreGeometry(geometry):
            self.resize(1380, 820)
        root = self.settings.value("root")
        if root and Path(root).is_dir():
            self.open_folder(root, restore_case=self.settings.value("case"))
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
        panels = QWidget()
        panels.setStyleSheet("background: #1a1a1a;")
        row = QHBoxLayout(panels)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(2)
        self.panels = {"source": SlicePanel(), "ct": SlicePanel()}
        for name, panel in self.panels.items():
            row.addWidget(panel)
            panel.hovered.connect(lambda xyz, n=name: self._hovered(n, xyz))
            panel.sliceScrolled.connect(self.step_slice)
            panel.fitRequested.connect(self.fit_view)
            panel.view.installEventFilter(self)
            panel.set_message("Select a case")
        self.panels["ct"].vb.setXLink(self.panels["source"].vb)
        self.panels["ct"].vb.setYLink(self.panels["source"].vb)
        v.addWidget(panels, 1)

        bar = QWidget()
        b = QHBoxLayout(bar)
        b.setContentsMargins(14, 8, 14, 8)
        self.slider = QSlider(Qt.Orientation.Horizontal)
        self.slider.valueChanged.connect(self.set_slice)
        self.slider_label = QLabel()
        self.slider_label.setMinimumWidth(130)
        self.slider_label.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        b.addWidget(QLabel("Slice"))
        b.addWidget(self.slider, 1)
        b.addWidget(self.slider_label)
        v.addWidget(bar)

        self.splitter = QSplitter()
        self.splitter.addWidget(sidebar)
        self.splitter.addWidget(viewer)
        self.splitter.setStretchFactor(1, 1)
        self.splitter.setSizes([250, 1100])
        state = self.settings.value("splitter")
        if state is not None:
            self.splitter.restoreState(state)
        self.stack.addWidget(self.splitter)

        self.hover_label = QLabel()
        self.case_label = QLabel()
        self.case_label.setStyleSheet("color: palette(placeholder-text);")
        self.statusBar().addWidget(self.hover_label, 1)
        self.statusBar().addPermanentWidget(self.case_label)
        self.statusBar().setSizeGripEnabled(False)

    def _build_actions(self):
        st = self.style()
        self.act_open = QAction("Open Dataset Folder…", self, shortcut=QKeySequence.StandardKey.Open,
                                triggered=self.choose_folder)
        self.act_prev = QAction(st.standardIcon(QStyle.StandardPixmap.SP_ArrowBack),
                                "Previous Case", self, triggered=lambda: self.step_case(-1))
        self.act_prev.setShortcuts([QKeySequence("Ctrl+["), QKeySequence(Qt.Key.Key_PageUp)])
        self.act_next = QAction(st.standardIcon(QStyle.StandardPixmap.SP_ArrowForward),
                                "Next Case", self, triggered=lambda: self.step_case(1))
        self.act_next.setShortcuts([QKeySequence("Ctrl+]"), QKeySequence(Qt.Key.Key_PageDown)])
        self.act_fit = QAction("Fit to Window", self, shortcut=QKeySequence("Ctrl+0"),
                               triggered=self.fit_view)
        self.act_reset_window = QAction("Reset Brightness/Contrast", self,
                                        shortcut=QKeySequence("Ctrl+R"),
                                        triggered=self.apply_window)
        self.act_find = QAction("Search Cases", self, shortcut=QKeySequence.StandardKey.Find,
                                triggered=lambda: (self.search.setFocus(), self.search.selectAll()))

        self.plane_group = QActionGroup(self)
        self.plane_actions = {}
        for i, plane in enumerate(PLANES, start=1):
            a = QAction(plane.capitalize(), self, checkable=True,
                        shortcut=QKeySequence(f"Ctrl+{i}"))
            a.setChecked(plane == self.plane)
            a.triggered.connect(lambda _=False, p=plane: self.set_plane(p))
            self.plane_group.addAction(a)
            self.plane_actions[plane] = a

        self.preset_group = QActionGroup(self)
        self.preset_actions = {}
        for name in [*PRESETS, AUTO]:
            a = QAction(name, self, checkable=True)
            a.setChecked(name == self.preset)
            a.triggered.connect(lambda _=False, n=name: self.set_preset(n))
            self.preset_group.addAction(a)
            self.preset_actions[name] = a

    def _build_menus(self):
        m = self.menuBar()
        file = m.addMenu("File")
        file.addAction(self.act_open)
        self.recent_menu = file.addMenu("Open Recent")
        self._refresh_recent()

        view = m.addMenu("View")
        for a in self.plane_actions.values():
            view.addAction(a)
        view.addSeparator()
        window = view.addMenu("CT Window")
        for a in self.preset_actions.values():
            window.addAction(a)
        view.addAction(self.act_reset_window)
        view.addSeparator()
        view.addAction(self.act_fit)

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

    def _build_toolbar(self):
        tb = QToolBar("Toolbar")
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
        for a in self.plane_actions.values():
            a.setToolTip(f"{a.text()} view  ({a.shortcut().toString(QKeySequence.SequenceFormat.NativeText)})")
            tb.addAction(a)
        tb.addSeparator()
        tb.addWidget(QLabel("  CT window  "))
        self.preset_combo = QComboBox()
        self.preset_combo.addItems([*PRESETS, AUTO])
        self.preset_combo.setCurrentText(self.preset)
        self.preset_combo.setToolTip("Brightness and contrast for CT. MRI and CBCT are "
                                     "adjusted automatically per image.\nRight-drag on any "
                                     "image to fine-tune.")
        self.preset_combo.textActivated.connect(self.set_preset)
        tb.addWidget(self.preset_combo)
        spacer = QWidget()
        spacer.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        tb.addWidget(spacer)
        fit = QAction("Fit", self, triggered=self.fit_view)
        fit.setToolTip("Fit image to window  (⌘0 or double-click)")
        tb.addAction(fit)
        self.toolbar = tb

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
        for panel in self.panels.values():
            panel.set_message("Loading…")
        self.case_label.setText(case.case_id)
        self._start_load(case)

    def _start_load(self, case: Case):
        if case.key in self.in_flight or self.cache.get(case.key) is not None:
            return
        self.in_flight.add(case.key)
        self.pool.start(_LoadTask(case, self.cache, self.signals))

    def _loaded(self, case: Case, loaded: LoadedCase | None, error: str):
        self.in_flight.discard(case.key)
        if self.wanted is None or case.key != self.wanted.key:
            return
        if loaded is None:
            self.current = None
            for panel in self.panels.values():
                panel.set_message("This case could not be opened")
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
        first = self.current is None
        old_shape = self.current.ct.data.shape if self.current else None
        self.current = loaded
        case = loaded.case
        self.panels["source"].set_title(case.source_name)
        self.panels["ct"].set_title("CT")
        for panel in self.panels.values():
            panel.set_message(None)
        self.setWindowTitle(f"{case.case_id} — {APP_NAME}")
        x, y, z = loaded.ct.shape_xyz
        sx, sy, sz = loaded.ct.spacing
        self.case_label.setText(f"{case.case_id}  ·  {case.region_name}  ·  {x} × {y} × {z}"
                                f"  ·  {sx:g} × {sy:g} × {sz:g} mm")
        self.hover_label.clear()
        self.apply_window()
        self._configure_slider()
        self._render()
        if first or old_shape != loaded.ct.data.shape:
            self.fit_view()
        self._prefetch()

    # -- display -----------------------------------------------------------------------

    def _volumes(self):
        if self.current is None:
            return {}
        return {"source": self.current.source, "ct": self.current.ct}

    def _configure_slider(self):
        n = self.current.ct.n_slices(self.plane)
        self.index = min(n - 1, max(0, round(self.position[self.plane] * (n - 1))))
        self.slider.blockSignals(True)
        self.slider.setRange(0, n - 1)
        self.slider.setValue(self.index)
        self.slider.blockSignals(False)
        self.slider.setPageStep(max(1, n // 10))

    def _render(self):
        if self.current is None:
            return
        for name, volume in self._volumes().items():
            index = min(self.index, volume.n_slices(self.plane) - 1)
            self.panels[name].set_content(volume, self.plane, index)
        n = self.current.ct.n_slices(self.plane)
        self.slider_label.setText(f"{self.index + 1} of {n}")
        self.position[self.plane] = self.index / max(1, n - 1)

    def set_slice(self, index: int):
        if self.current is None:
            return
        n = self.current.ct.n_slices(self.plane)
        index = max(0, min(n - 1, index))
        if index != self.slider.value():
            self.slider.setValue(index)  # re-enters via valueChanged
            return
        self.index = index
        self._render()
        for panel in self.panels.values():
            panel.marker.hide()

    def step_slice(self, step: int):
        self.set_slice(self.index + step)

    def set_plane(self, plane: str):
        self.plane_actions[plane].setChecked(True)
        if plane == self.plane:
            return
        self.plane = plane
        self.settings.setValue("plane", plane)
        if self.current is not None:
            self._configure_slider()
            self._render()
            self.fit_view()

    def set_preset(self, name: str):
        self.preset = name
        self.settings.setValue("preset", name)
        self.preset_actions[name].setChecked(True)
        self.preset_combo.setCurrentText(name)
        self.apply_window()

    def apply_window(self):
        if self.current is None:
            return
        for name, panel in self.panels.items():
            # MRI and CBCT values are not calibrated HU (center D's CBCT sits hundreds of
            # HU below its CT), so they are always windowed per image.
            if name == "source" or self.preset == AUTO:
                panel.set_levels(*self.current.auto[name])
            else:
                panel.set_levels(*preset_levels(self.preset))

    def fit_view(self):
        if self.current is not None:
            self.panels["source"].fit()

    def _hovered(self, name: str, xyz):
        for other, panel in self.panels.items():
            panel.show_marker(xyz if other != name else None)
        if xyz is None or self.current is None:
            self.hover_label.clear()
            return
        parts = []
        for key, volume in self._volumes().items():
            if volume.contains(xyz):
                label = self.current.case.source_name if key == "source" else "CT"
                unit = "" if label == "MRI" else " HU"
                parts.append(f"<b>{label}</b> {_fmt(volume.value(xyz))}{unit}")
        x, y, z = xyz
        parts.append(f"<span style='color: gray'>voxel {x}, {y}, {z}</span>")
        self.hover_label.setText("&nbsp;&nbsp;&nbsp;&nbsp;".join(parts))

    # -- misc --------------------------------------------------------------------------

    def _update_enabled(self):
        has_data = self.stack.currentWidget() is self.splitter and bool(self.cases)
        for a in (self.act_prev, self.act_next, self.act_fit, self.act_reset_window,
                  self.act_find, *self.case_nav, *self.plane_actions.values()):
            a.setEnabled(has_data)
        if hasattr(self, "toolbar"):
            self.toolbar.setVisible(has_data)
        self.statusBar().setVisible(has_data)

    def eventFilter(self, obj, ev):
        # Arrow keys scroll slices while an image has focus (the case list keeps its own).
        if ev.type() == ev.Type.KeyPress and self.current is not None:
            key, shift = ev.key(), ev.modifiers() & Qt.KeyboardModifier.ShiftModifier
            step = 10 if shift else 1
            if key in (Qt.Key.Key_Up, Qt.Key.Key_Right):
                self.step_slice(step)
                return True
            if key in (Qt.Key.Key_Down, Qt.Key.Key_Left):
                self.step_slice(-step)
                return True
            if key == Qt.Key.Key_Home:
                self.set_slice(0)
                return True
            if key == Qt.Key.Key_End:
                self.set_slice(self.current.ct.n_slices(self.plane) - 1)
                return True
        return super().eventFilter(obj, ev)

    def show_controls(self):
        rows = [
            ("Scroll", "Move through slices"),
            ("↑ ↓  (click an image first)", "Previous / next slice — hold ⇧ for steps of 10"),
            ("Home / End", "First / last slice"),
            ("Pinch, or ⌘ + scroll", "Zoom"),
            ("Drag", "Move the image"),
            ("Right-drag", "Brightness (up/down) and contrast (left/right)"),
            ("Double-click, ⌘0", "Fit image to window"),
            ("⌘R", "Reset brightness and contrast"),
            ("⌘1  ⌘2  ⌘3", "Axial, coronal, sagittal view"),
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
        self.settings.setValue("geometry", self.saveGeometry())
        self.settings.setValue("splitter", self.splitter.saveState())
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
