"""Scan details from the organisers' ``overviews/*_train_parameters.xlsx`` spreadsheets.

Each region folder has one workbook with a sheet per modality (MR or CBCT, and CT), one
row per case, DICOM header fields as columns. Missing values are written as "empty" or
"-", and a few numbers are garbled (an MR repetition time stored as a date, decimal
points lost), so every number is range-checked before it is shown.
"""
from __future__ import annotations

import threading
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path

from .dataset import Case

SHEETS = {"mr": "MR", "cbct": "CBCT", "ct": "CT"}
_MISSING = {"", "-", "empty", "none", "nan"}


@dataclass
class CaseInfo:
    patient: list[tuple[str, str]] = field(default_factory=list)
    scans: dict[str, list[tuple[str, str]]] = field(default_factory=dict)  # title -> rows
    overview: Path | None = None


def overview_path(case: Case) -> Path | None:
    path = case.path.parent / "overviews" / f"{case.case_id}_overview.png"
    return path if path.exists() else None


def _clean(value):
    if value is None:
        return None
    if isinstance(value, str):
        value = value.strip()
        return None if value.lower() in _MISSING else value
    return value


def _number(value, lo: float, hi: float) -> float | None:
    value = _clean(value)
    if isinstance(value, bool) or not isinstance(value, (int, float, str)):
        return None
    try:
        x = float(value)
    except ValueError:
        return None
    return x if lo <= x <= hi else None


def _fmt(x: float, unit: str = "") -> str:
    text = f"{x:.3g}" if abs(x) < 100 else f"{x:.0f}"
    return f"{text} {unit}".strip()


def _date(value) -> date | None:
    value = _clean(value)
    if isinstance(value, datetime):
        return value.date()
    try:
        return datetime.strptime(str(int(value)), "%Y%m%d").date()
    except (TypeError, ValueError):
        return None


def _pair(value, lo: float, hi: float, unit: str) -> str | None:
    """'[0.9765625, 0.9765625]' or '(1.0, 1.0, 3.0)' -> '0.977 × 0.977 mm'."""
    value = _clean(value)
    if not isinstance(value, str):
        return None
    parts = [_number(p, lo, hi) for p in value.strip("[]() ").split(",")]
    if not parts or any(p is None for p in parts):
        return None
    return " × ".join(_fmt(p) for p in parts) + (f" {unit}" if unit else "")


def _scanner(row: dict) -> str | None:
    make, model = _clean(row.get("Manufacturer")), _clean(row.get("ManufacturerModelName"))
    if make and model and str(model).lower().startswith(str(make).lower()):
        make = None
    text = " ".join(str(p) for p in (make and str(make).title(), model) if p)
    return text or None


def _scan_rows(kind: str, row: dict) -> list[tuple[str, str]]:
    rows: list[tuple[str, str | None]] = [
        ("Scanner", _scanner(row)),
        ("Date", (d := _date(row.get("StudyDate"))) and d.strftime("%-d %b %Y")),
        ("Series", _clean(row.get("SeriesDescription"))),
    ]
    if kind == "mr":
        field_t = _number(row.get("MagneticFieldStrength"), 0.1, 12)
        te = _number(row.get("EchoTime"), 0.1, 500)
        tr = _number(row.get("RepetitionTime"), 0.5, 20000)
        flip = _number(row.get("FlipAngle"), 1, 180)
        acq = _clean(row.get("MRAcquisitionType"))
        seq = _clean(row.get("ScanningSequence"))
        rows += [
            ("Field strength", field_t and _fmt(field_t, "T")),
            ("Sequence", " ".join(str(p) for p in (acq, seq) if p) or None),
            ("Echo time", te and _fmt(te, "ms")),
            ("Repetition time", tr and _fmt(tr, "ms")),
            ("Flip angle", flip and f"{flip:g}°"),
        ]
    else:
        kv = _number(row.get("KVP"), 40, 160)
        ma = _number(row.get("XRayTubeCurrent"), 1, 2000)
        ms = _number(row.get("ExposureTime"), 1, 100000)
        rows += [
            ("Tube voltage", kv and _fmt(kv, "kV")),
            ("Tube current", ma and _fmt(ma, "mA")),
            ("Exposure time", ms and _fmt(ms, "ms")),
        ]
    thickness = _number(row.get("SliceThickness"), 0.1, 20)
    cols, rows_px = _number(row.get("Columns"), 8, 4096), _number(row.get("Rows"), 8, 4096)
    slices = _number(row.get("Slices"), 1, 5000)
    size = (f"{cols:.0f} × {rows_px:.0f}" + (f" × {slices:.0f}" if slices else "")
            if cols and rows_px else None)
    rows += [
        ("Pixel spacing", _pair(row.get("PixelSpacing"), 0.05, 10, "mm")),
        ("Slice thickness", thickness and _fmt(thickness, "mm")),
        ("Original size", size),
    ]
    return [(k, str(v)) for k, v in rows if v]


def _patient_rows(rows: list[dict]) -> list[tuple[str, str]]:
    out = []
    sexes = {"M": "Male", "F": "Female", "O": "Other"}
    sex = next((sexes.get(str(_clean(r.get("PatientSex")))) for r in rows
                if _clean(r.get("PatientSex")) in sexes), None)
    if sex:
        out.append(("Sex", sex))
    for r in rows:
        age = _clean(r.get("PatientAge"))
        if isinstance(age, str) and age[:-1].isdigit() and age[-1] in "YM":
            n = int(age[:-1])
            out.append(("Age", f"{n} years" if age[-1] == "Y" else f"{n} months"))
            break
    weight = next((w for r in rows if (w := _number(r.get("PatientWeight"), 2, 300))), None)
    if weight:
        out.append(("Weight", _fmt(weight, "kg")))
    return out


class MetadataIndex:
    """Reads each region's workbook once, on first use (thread-safe)."""

    def __init__(self):
        self._books: dict[Path, dict[str, dict[str, dict]]] = {}
        self._lock = threading.Lock()

    def _book(self, folder: Path) -> dict[str, dict[str, dict]]:
        with self._lock:
            if folder not in self._books:
                self._books[folder] = _read_workbooks(folder)
            return self._books[folder]

    def info(self, case: Case) -> CaseInfo:
        book = self._book(case.path.parent / "overviews")
        rows = {kind: book.get(SHEETS[kind], {}).get(case.case_id)
                for kind in (case.source, "ct")}
        info = CaseInfo(overview=overview_path(case))
        found = [r for r in rows.values() if r]
        info.patient = _patient_rows(found)
        for kind in (case.source, "ct"):
            if rows[kind]:
                info.scans[{"mr": "MRI", "cbct": "CBCT", "ct": "CT"}[kind]] = \
                    _scan_rows(kind, rows[kind])
        d_src = rows[case.source] and _date(rows[case.source].get("StudyDate"))
        d_ct = rows["ct"] and _date(rows["ct"].get("StudyDate"))
        if d_src and d_ct:
            days = (d_src - d_ct).days
            name = case.source_name
            when = ("on the same day as the CT" if days == 0 else
                    f"{abs(days)} day{'s' if abs(days) != 1 else ''} "
                    f"{'after' if days > 0 else 'before'} the CT")
            info.patient.append(("Scans", f"{name} {when}"))
        return info


def _read_workbooks(folder: Path) -> dict[str, dict[str, dict]]:
    """{sheet name: {case id: {column: value}}} for every workbook in ``folder``."""
    out: dict[str, dict[str, dict]] = {}
    if not folder.is_dir():
        return out
    try:
        import openpyxl
    except ImportError:
        return out
    for path in sorted(folder.glob("*.xlsx")):
        if path.name.startswith("~$"):
            continue
        try:
            wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
        except Exception:
            continue
        for ws in wb.worksheets:
            rows = ws.iter_rows(values_only=True)
            header = next(rows, None)
            if not header or "ID" not in header:
                continue
            header = [str(h) if h is not None else "" for h in header]
            sheet = out.setdefault(ws.title, {})
            for row in rows:
                record = dict(zip(header, row))
                if record.get("ID"):
                    sheet[str(record["ID"])] = record
        wb.close()
    return out
