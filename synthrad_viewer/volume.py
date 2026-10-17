"""Volumes, slicing for the three planes, and intensity windows.

Every volume is reoriented so its index axes run toward patient Left, Posterior and
Superior (identity direction). Slices are then laid out radiologically: the patient's
right on screen left, anterior or superior at the top. Display coordinates (u, v) are
millimetres from the slice's top-left corner, so anisotropic voxels (1 x 1 x 3 mm in
SynthRAD) are drawn at their true size.
"""
from __future__ import annotations

import threading
from collections import OrderedDict
from dataclasses import dataclass, field

import numpy as np
import SimpleITK as sitk

from .dataset import Case

PLANES = ("axial", "coronal", "sagittal")

# Screen edge -> patient direction, per plane.
ORIENTATION_LABELS = {
    "axial": {"left": "R", "right": "L", "top": "A", "bottom": "P"},
    "coronal": {"left": "R", "right": "L", "top": "S", "bottom": "I"},
    "sagittal": {"left": "A", "right": "P", "top": "S", "bottom": "I"},
}

# CT window presets as (width, level) in HU. MRI and CBCT are always windowed automatically.
PRESETS = {
    "Soft tissue": (400, 40),
    "Bone": (1800, 400),
    "Lung": (1500, -600),
    "Brain": (80, 40),
}
AUTO = "Automatic"


def preset_levels(name: str) -> tuple[float, float]:
    width, level = PRESETS[name]
    return level - width / 2, level + width / 2


@dataclass
class Volume:
    data: np.ndarray  # (z, y, x)
    spacing: tuple[float, float, float]  # (x, y, z) mm
    origin: tuple[float, float, float]  # (x, y, z) mm

    @property
    def shape_xyz(self) -> tuple[int, int, int]:
        z, y, x = self.data.shape
        return x, y, z

    def n_slices(self, plane: str) -> int:
        x, y, z = self.shape_xyz
        return {"axial": z, "coronal": y, "sagittal": x}[plane]

    def pixel_size(self, plane: str) -> tuple[float, float]:
        sx, sy, sz = self.spacing
        return {"axial": (sx, sy), "coronal": (sx, sz), "sagittal": (sy, sz)}[plane]

    def extent_mm(self, plane: str) -> tuple[float, float]:
        rows, cols = self.slice(plane, 0).shape
        du, dv = self.pixel_size(plane)
        return cols * du, rows * dv

    def slice(self, plane: str, index: int) -> np.ndarray:
        """2-D slice, row 0 at the top of the screen."""
        if plane == "axial":
            s = self.data[index]
        elif plane == "coronal":
            s = self.data[::-1, index, :]
        else:
            s = self.data[::-1, :, index]
        return np.ascontiguousarray(s)

    def to_voxel(self, plane: str, index: int, u: float, v: float):
        """Display point (mm) on slice ``index`` -> voxel (x, y, z), or None outside."""
        du, dv = self.pixel_size(plane)
        col, row = int(np.floor(u / du)), int(np.floor(v / dv))
        nz = self.data.shape[0]
        if plane == "axial":
            xyz = (col, row, index)
        elif plane == "coronal":
            xyz = (col, index, nz - 1 - row)
        else:
            xyz = (index, col, nz - 1 - row)
        return xyz if self.contains(xyz) else None

    def from_voxel(self, plane: str, xyz) -> tuple[float, float]:
        """Voxel (x, y, z) -> display point (mm) at the voxel centre."""
        x, y, z = xyz
        du, dv = self.pixel_size(plane)
        nz = self.data.shape[0]
        col, row = {"axial": (x, y), "coronal": (x, nz - 1 - z),
                    "sagittal": (y, nz - 1 - z)}[plane]
        return (col + 0.5) * du, (row + 0.5) * dv

    def contains(self, xyz) -> bool:
        return all(0 <= c < n for c, n in zip(xyz, self.shape_xyz))

    def value(self, xyz):
        x, y, z = xyz
        return self.data[z, y, x]


def load_volume(path) -> Volume:
    image = sitk.DICOMOrient(sitk.ReadImage(str(path)), "LPS")
    return Volume(sitk.GetArrayFromImage(image), tuple(image.GetSpacing()),
                  tuple(image.GetOrigin()))


def auto_levels(data: np.ndarray, mask: np.ndarray | None = None,
                low: float = 1.0, high: float = 99.5) -> tuple[float, float]:
    """Percentile window inside the body mask, ignoring padding at the minimum value
    (CBCT is filled with -1024 outside its field of view)."""
    if mask is not None and mask.shape == data.shape and mask.any():
        values = data[mask]
    else:
        values = data.ravel()
    floor = values.min()
    inside = values[values > floor]
    if inside.size > values.size * 0.05:
        values = inside
    if values.size > 2_000_000:
        values = values[:: values.size // 2_000_000 + 1]
    lo, hi = (float(p) for p in np.percentile(values, [low, high]))
    return (lo, hi) if hi > lo else (lo, lo + 1.0)


@dataclass
class LoadedCase:
    case: Case
    source: Volume  # MRI or CBCT
    ct: Volume
    mask: np.ndarray | None
    auto: dict[str, tuple[float, float]] = field(default_factory=dict)  # "source"/"ct"


def load_case(case: Case) -> LoadedCase:
    source = load_volume(case.file(case.source))
    ct = load_volume(case.file("ct"))
    mask = None
    if case.file("mask").exists():
        m = load_volume(case.file("mask")).data > 0
        if m.shape == ct.data.shape:
            mask = m
    return LoadedCase(case, source, ct, mask, {
        "source": auto_levels(source.data, mask),
        "ct": auto_levels(ct.data, mask),
    })


class CaseCache:
    """Small thread-safe LRU of loaded cases (about 40 MB each)."""

    def __init__(self, capacity: int = 6):
        self.capacity = capacity
        self._items: OrderedDict[str, LoadedCase] = OrderedDict()
        self._lock = threading.Lock()

    def get(self, key: str) -> LoadedCase | None:
        with self._lock:
            item = self._items.get(key)
            if item is not None:
                self._items.move_to_end(key)
            return item

    def put(self, key: str, item: LoadedCase) -> None:
        with self._lock:
            self._items[key] = item
            self._items.move_to_end(key)
            while len(self._items) > self.capacity:
                self._items.popitem(last=False)
