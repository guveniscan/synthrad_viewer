# SynthRAD Viewer

A desktop app for browsing the [SynthRAD2025](https://synthrad2025.grand-challenge.org/)
challenge data: MRI → CT (Task 1) and CBCT → CT (Task 2) side by side, slice by slice,
with comparison views for checking how well each pair lines up.

> Unofficial community tool, not affiliated with the SynthRAD organisers. For research
> use only, not for clinical decisions.

## Features

- **Case browser:** every case in a folder, grouped by task and region, with search.
  Moving through the list is instant because neighbouring cases load in the background.
- **Linked views:** MRI or CBCT next to CT, always on the same slice, with linked zoom and
  pan. Axial, coronal or sagittal at true voxel proportions, or all three planes at once
  joined by one crosshair (click anywhere to move it).
- **Compare:** CT − CBCT difference map in HU, a checkerboard, or a colour overlay
  (MRI/CBCT green, CT magenta, grey where they agree) to spot registration errors.
- **Body mask** shown as an outline, a fill, or by dimming everything outside it.
- **Windowing:** CT presets (soft tissue, bone, lung, brain), automatic per-image windows
  for MRI and CBCT (their values are not calibrated HU), right-drag to fine-tune.
- **Hover readout:** the value under the cursor in every scan, and CT − CBCT.
- **Case details:** patient and scanner information from the organisers' spreadsheets,
  intensity histograms with a draggable window, and the organisers' overview figure.

## Getting the data

The data is not included. Register for the challenge on
[Grand Challenge](https://synthrad2025.grand-challenge.org/) to download it, unzip it, and
open the folder in the viewer. It finds every case folder inside it: a folder holding
`ct.mha` together with `mr.mha` (Task 1) or `cbct.mha` (Task 2), plus `mask.mha`. The
spreadsheets and overview images in each region's `overviews/` folder are used when
present. The data comes with its own license; please follow it.

## Install

### macOS

```bash
git clone https://github.com/guveniscan/synthrad_viewer.git
cd synthrad_viewer
scripts/install_mac_app.sh
```

This installs **SynthRAD Viewer** into `/Applications`, so it opens from Launchpad,
Spotlight or the Dock like any other app. It needs Python 3.10 or newer
(`brew install python@3.12`) and Apple's command line tools (`xcode-select --install`).
Code changes in the folder take effect the next time the app starts. Run the script again
if you move the folder or upgrade Python.

### Linux and Windows

```bash
pip install git+https://github.com/guveniscan/synthrad_viewer.git
synthrad-viewer
```

The tests run on Linux as well as macOS, but day-to-day use has been on macOS.

## Using it

On first launch choose **Open Dataset Folder…**. The viewer reopens the same folder and
case next time. **Help → Mouse and Keyboard Controls** lists every shortcut; the main ones:

| | |
|---|---|
| Scroll | move through slices |
| Click | move the crosshair; the other planes follow |
| ↑ ↓ (after clicking an image), Home / End | slice by slice, ⇧ for steps of 10 |
| ↑ ↓ in the case list, Page Up / Page Down | previous / next case |
| ⌘1 ⌘2 ⌘3 / ⌘4 | axial, coronal, sagittal / all three planes |
| Pinch or ⌘-scroll, drag, double-click | zoom, pan, fit |
| Right-drag | brightness (up/down) and contrast (left/right) |
| ⌘I | case details |
| ⌘F | search cases |

(On Linux and Windows, use Ctrl in place of ⌘.)

The hover readout shows the values stored in the files: HU for CT and CBCT, and the
scanner's arbitrary units for MRI. Windowing changes only how images are drawn.

## Development

```bash
python3 -m venv .venv && .venv/bin/pip install -e ".[dev]"
.venv/bin/python -m synthrad_viewer     # run from source
.venv/bin/python -m pytest              # tests (no data needed)
```

`dataset.py` (finding cases), `volume.py` (loading, slicing, windows, comparison images)
and `metadata.py` (spreadsheets) have no Qt dependency. `views.py` is one image panel,
`viewer.py` the grid of panels with the shared crosshair, `details.py` the side panel and
`app.py` the main window.

## License

[MIT](LICENSE)
