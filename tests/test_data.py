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


def test_comparison_images():
    from synthrad_viewer.volume import checkerboard, colour_overlay, difference, normalise

    ct = np.array([[100, 200], [300, 400]], np.int16)
    cbct = np.array([[50, 250], [300, 0]], np.int16)
    mask = np.array([[True, True], [False, True]])
    assert difference(ct, cbct, mask).tolist() == [[50, -50], [0, 400]]
    a, b = normalise(cbct, (0, 400)), normalise(ct, (0, 400))
    assert normalise(np.array([-10, 200, 999]), (0, 400)).tolist() == [0, 0.5, 1]
    board = checkerboard(np.zeros((4, 4)), np.ones((4, 4)), (2, 2))
    assert board[:, 0].tolist() == [0, 0, 1, 1] and board[0].tolist() == [0, 0, 1, 1]
    rgb = colour_overlay(b, a)
    assert rgb.shape == (2, 2, 3) and np.array_equal(rgb[..., 0], b) and np.array_equal(rgb[..., 1], a)


def test_mask_overlay_outline():
    from synthrad_viewer.views import mask_overlay

    m = np.zeros((5, 5), bool)
    m[1:4, 1:4] = True
    outline = mask_overlay(m, "outline")[..., 3] > 0
    assert outline.sum() == 8 and not outline[2, 2]  # ring around the centre
    assert (mask_overlay(m, "dim")[..., 3] > 0).sum() == 25 - 9
    assert mask_overlay(m, "off") is None


def test_metadata_formats_and_rejects_garbage(tmp_path):
    import openpyxl

    from synthrad_viewer.dataset import Case
    from synthrad_viewer.metadata import MetadataIndex

    region = tmp_path / "Task2" / "AB"
    (region / "overviews").mkdir(parents=True)
    wb = openpyxl.Workbook()
    header = ["ID", "PatientSex", "PatientAge", "StudyDate", "KVP", "SliceThickness",
              "Manufacturer", "ManufacturerModelName", "PixelSpacing"]
    cbct = wb.active
    cbct.title = "CBCT"
    cbct.append(header)
    cbct.append(["2ABD001", "M", "empty", 20240103, 120, 89999997615814, "ELEKTA",
                 "Elekta XVI", "[1, 1]"])
    ct = wb.create_sheet("CT")
    ct.append(header)
    ct.append(["2ABD001", "M", "077Y", 20231229, "-", 2, "SIEMENS", "SOMATOM", "[0.98, 0.98]"])
    wb.save(region / "overviews" / "2_AB_train_parameters.xlsx")

    info = MetadataIndex().info(Case("2ABD001", 2, "AB", region / "2ABD001", "cbct"))
    assert dict(info.patient) == {"Sex": "Male", "Age": "77 years",
                                  "Scans": "CBCT 5 days after the CT"}
    cbct_rows = dict(info.scans["CBCT"])
    assert cbct_rows["Scanner"] == "Elekta XVI" and cbct_rows["Tube voltage"] == "120 kV"
    assert "Slice thickness" not in cbct_rows  # garbled value dropped
    ct_rows = dict(info.scans["CT"])
    assert ct_rows["Scanner"] == "Siemens SOMATOM" and "Tube voltage" not in ct_rows
    assert info.overview is None
