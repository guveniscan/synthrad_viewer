import numpy as np
import pytest
import SimpleITK as sitk

from synthrad_viewer.dataset import scan
from synthrad_viewer.volume import PLANES, auto_levels, load_case


def _write(path, array, spacing=(1.0, 1.0, 3.0)):
    image = sitk.GetImageFromArray(array)
    image.SetSpacing(spacing)
    sitk.WriteImage(image, str(path), useCompression=True)


@pytest.fixture
def dataset(tmp_path):
    rng = np.random.default_rng(0)
    layout = {"synthRAD2025_Task1_Train_D/Task1/HN/1HND001": "mr",
              "synthRAD2025_Task2_Train_D/Task2/AB/2ABD003": "cbct",
              "synthRAD2025_Task2_Train_D/Task2/HN/2HND002": "cbct",
              "merged/TH/2THD005": "cbct"}
    for rel, source in layout.items():
        d = tmp_path / rel
        d.mkdir(parents=True)
        vol = rng.integers(-1000, 1000, (6, 5, 4)).astype(np.int16)  # z, y, x
        mask = np.zeros_like(vol)
        mask[1:5, 1:4, 1:3] = 1
        for name, data in ((source, vol), ("ct", vol), ("mask", mask)):
            _write(d / f"{name}.mha", data)
    (tmp_path / "synthRAD2025_Task2_Train_D/Task2/AB/overviews").mkdir()
    (tmp_path / "incomplete").mkdir()
    _write(tmp_path / "incomplete/ct.mha", np.zeros((2, 2, 2), np.int16))
    return tmp_path


def test_scan_finds_and_orders_cases(dataset):
    cases = scan(dataset)
    assert [(c.case_id, c.task, c.region, c.source) for c in cases] == [
        ("1HND001", 1, "HN", "mr"),
        ("2HND002", 2, "HN", "cbct"),
        ("2THD005", 2, "TH", "cbct"),
        ("2ABD003", 2, "AB", "cbct"),
    ]


def test_slices_and_voxel_round_trip(dataset):
    loaded = load_case(scan(dataset)[0])
    vol = loaded.ct
    assert vol.shape_xyz == (4, 5, 6)
    assert {p: vol.n_slices(p) for p in PLANES} == {"axial": 6, "coronal": 5, "sagittal": 4}
    assert vol.extent_mm("sagittal") == (5.0, 18.0)  # y by z, z at 3 mm
    for plane in PLANES:
        for xyz in [(0, 0, 0), (3, 4, 5), (2, 1, 3)]:
            index = {"axial": xyz[2], "coronal": xyz[1], "sagittal": xyz[0]}[plane]
            u, v = vol.from_voxel(plane, xyz)
            assert vol.to_voxel(plane, index, u, v) == xyz
            # the displayed pixel holds that voxel's value
            du, dv = vol.pixel_size(plane)
            assert vol.slice(plane, index)[int(v // dv), int(u // du)] == vol.value(xyz)
    # superior is at the top of coronal/sagittal slices
    assert np.array_equal(vol.slice("coronal", 0)[0], vol.data[-1, 0, :])
    assert vol.to_voxel("axial", 0, -1, 0) is None


def test_auto_levels_ignore_padding_and_use_mask():
    data = np.full((10, 10, 10), -1024, np.int16)
    data[2:8, 2:8, 2:8] = np.arange(216).reshape(6, 6, 6)
    lo, hi = auto_levels(data, mask=None, low=0, high=100)
    assert (lo, hi) == (0.0, 215.0)  # the -1024 padding is dropped
    mask = np.zeros(data.shape, bool)
    mask[2:4, 2:8, 2:8] = True
    assert auto_levels(data, mask, low=0, high=100)[1] == 71.0
