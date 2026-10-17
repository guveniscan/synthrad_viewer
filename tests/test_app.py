"""Opens the main window offscreen on a synthetic dataset and exercises the main views."""
import os
import time

import numpy as np
import pytest
import SimpleITK as sitk

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QSettings  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402


def _write(path, array):
    image = sitk.GetImageFromArray(array)
    image.SetSpacing((1.0, 1.0, 3.0))
    sitk.WriteImage(image, str(path), useCompression=True)


@pytest.fixture
def window(tmp_path):
    import pyqtgraph as pg

    from synthrad_viewer import app as A

    rng = np.random.default_rng(1)
    for rel, source in (("Task1/HN/1HNX001", "mr"), ("Task2/AB/2ABX001", "cbct"),
                        ("Task2/AB/2ABX002", "cbct")):
        d = tmp_path / "data" / rel
        d.mkdir(parents=True)
        vol = rng.integers(-1000, 1000, (8, 20, 24)).astype(np.int16)
        mask = np.zeros_like(vol)
        mask[2:6, 4:16, 4:20] = 1
        for name, data in ((source, vol), ("ct", vol + 50), ("mask", mask)):
            _write(d / f"{name}.mha", data)

    QSettings.setDefaultFormat(QSettings.Format.IniFormat)
    QSettings.setPath(QSettings.Format.IniFormat, QSettings.Scope.UserScope,
                      str(tmp_path / "settings"))
    pg.setConfigOptions(imageAxisOrder="row-major")
    qa = QApplication.instance() or QApplication([])
    qa.setOrganizationName("SynthRADTest")
    qa.setApplicationName("ViewerTest")
    w = A.MainWindow()
    w.show()
    w.open_folder(str(tmp_path / "data"))
    yield w, qa
    w.close()


def _wait(qa, condition, timeout=10.0):
    end = time.time() + timeout
    while not condition():
        assert time.time() < end, "timed out"
        qa.processEvents()
        time.sleep(0.01)


def test_browse_and_view(window):
    w, qa = window
    assert len(w.cases) == 3
    _wait(qa, lambda: w.current is not None)
    assert w.current.case.case_id == "1HNX001"
    assert w.grid.n_slices("axial") == 8

    w.set_layout("all")
    w.set_compare("overlay")
    w.set_mask_mode("outline")
    assert len(w.grid.visible_panels()) == 9

    w.grid.set_focus((3, 5, 2), "coronal")
    assert w.grid.active == "coronal" and w.grid.index("axial") == 2
    assert w.slider.value() == 5

    w.step_case(1)
    _wait(qa, lambda: w.current is not None and w.current.case.case_id == "2ABX001")
    w.set_compare("difference")
    diff = w.grid.panels[("compare", "axial")].image.image
    assert diff[6, 6] == 50 and diff[1, 1] == 0  # CT = CBCT + 50 in the body, 0 outside

    w.set_preset("Bone")
    assert w.grid.levels["ct"] == (-500.0, 1300.0)
    w.details.windowDragged.emit("ct", -100.0, 100.0)
    assert w.grid.panels[("ct", "axial")].levels == (-100.0, 100.0)
