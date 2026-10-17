# SynthRAD Viewer

A desktop app for browsing the [SynthRAD2025](https://synthrad2025.grand-challenge.org/) data:
MRI → CT (Task 1) and CBCT → CT (Task 2), side by side, slice by slice.

## Install (macOS, once)

```bash
brew install python@3.12          # if you don't have Python 3.11+
scripts/install_mac_app.sh
```

This installs **SynthRAD Viewer** into `~/Applications`, so it opens from Launchpad,
Spotlight or the Dock. Code changes in this folder take effect the next time the app
starts. Run the script again only if you move this folder or upgrade Python.

## Using it

On first launch choose **Open Dataset Folder…** and pick the folder you unzipped the data
into (for example `SynthRAD2025_dataset_D`). The viewer finds every case inside it and
reopens the same folder and case next time.

| | |
|---|---|
| Scroll | move through slices |
| ↑ ↓ (after clicking an image), Home / End | slice by slice, ⇧ for steps of 10 |
| ↑ ↓ in the case list, Page Up / Page Down | previous / next case |
| ⌘1 ⌘2 ⌘3 | axial, coronal, sagittal |
| Pinch or ⌘-scroll, drag, double-click | zoom, pan, fit |
| Right-drag | brightness (up/down) and contrast (left/right) |
| ⌘F | search cases |

| Click | move the crosshair; the other planes follow |
| ⌘4 | all three planes at once |
| ⌘I | case details |

The toolbar's **CT window** sets the CT display (soft tissue, bone, lung, brain).
MRI and CBCT are windowed per image because their values are not calibrated HU.
Hovering shows the values under the cursor in every image.

- **All Planes** shows axial, coronal and sagittal together, linked by one crosshair.
- **Compare** adds a row or column comparing the two scans: *Difference* (CT − CBCT in
  HU, CBCT cases only), *Checkerboard*, or *Colour overlay* (MRI/CBCT green, CT magenta,
  grey where they line up), which makes registration errors easy to spot.
- **Body mask** draws the mask the challenge scores inside: outline, filled, or dimming
  everything outside it.
- **Details** shows patient and scanner information from the organisers' spreadsheets,
  intensity histograms (drag the yellow band to change brightness/contrast) and the
  organisers' overview image.

## Development

```bash
.venv/bin/python -m synthrad_viewer     # run from source
.venv/bin/python -m pytest              # tests
```

`dataset.py` (finding cases), `volume.py` (loading, slicing, windows, comparison images)
and `metadata.py` (spreadsheets) have no Qt dependency. `views.py` is one image panel,
`viewer.py` the grid of panels with the shared crosshair, `details.py` the side panel and
`app.py` the main window.
