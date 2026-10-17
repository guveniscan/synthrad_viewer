"""Finding SynthRAD2025 cases on disk.

A case is any folder holding ``ct.mha`` plus ``mr.mha`` (Task 1) or ``cbct.mha`` (Task 2).
The scanner searches instead of assuming a layout, so it handles the official download
(``.../Task2/AB/2ABD001/``) as well as flattened copies (``merged/AB/2ABD001/``).
"""
from __future__ import annotations

import os
import re
from dataclasses import dataclass
from pathlib import Path

TASKS = {1: "MRI → CT", 2: "CBCT → CT"}
REGIONS = {"HN": "Head & Neck", "TH": "Thorax", "AB": "Abdomen"}
SOURCE_NAMES = {"mr": "MRI", "cbct": "CBCT"}

_SKIP_DIRS = {"overviews", "__MACOSX"}
_CASE_ID = re.compile(r"^([12])(AB|TH|HN)", re.IGNORECASE)


@dataclass(frozen=True)
class Case:
    case_id: str
    task: int
    region: str  # "AB", "TH", "HN" or "Other"
    path: Path
    source: str  # "mr" or "cbct"

    @property
    def key(self) -> str:
        return str(self.path)

    @property
    def region_name(self) -> str:
        return REGIONS.get(self.region, "Other")

    @property
    def source_name(self) -> str:
        return SOURCE_NAMES[self.source]

    def file(self, name: str) -> Path:
        return self.path / f"{name}.mha"


def _region_of(case_id: str, path: Path) -> str:
    m = _CASE_ID.match(case_id)
    if m:
        return m.group(2).upper()
    parent = path.parent.name.upper()
    return parent if parent in REGIONS else "Other"


def scan(root: str | os.PathLike, max_depth: int = 6) -> list[Case]:
    """All cases under ``root``, ordered by task, region (HN, TH, AB) and case id."""
    root = Path(root)
    cases: list[Case] = []
    base_depth = len(root.parts)
    for dirpath, dirnames, filenames in os.walk(root):
        path = Path(dirpath)
        files = set(filenames)
        source = "mr" if "mr.mha" in files else "cbct" if "cbct.mha" in files else None
        if source and "ct.mha" in files:
            cases.append(Case(path.name, 1 if source == "mr" else 2,
                              _region_of(path.name, path), path, source))
            dirnames[:] = []
            continue
        if len(path.parts) - base_depth >= max_depth:
            dirnames[:] = []
        else:
            dirnames[:] = sorted(d for d in dirnames
                                 if not d.startswith(".") and d not in _SKIP_DIRS)
    order = list(REGIONS) + ["Other"]
    cases.sort(key=lambda c: (c.task, order.index(c.region), c.case_id))
    return cases
