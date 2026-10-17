#!/bin/bash
# One-time setup: creates the Python environment and installs "SynthRAD Viewer" into
# /Applications, so it opens from Launchpad, Spotlight or the Dock like any other app.
# Run it again after moving this folder. Code changes need no reinstall.
set -euo pipefail
REPO="$(cd "$(dirname "$0")/.." && pwd)"
# /Applications when writable (the folder Finder's sidebar shows), else ~/Applications.
DEFAULT_DIR=/Applications
[ -w "$DEFAULT_DIR" ] || DEFAULT_DIR="$HOME/Applications"
APP="${APP_DIR:-$DEFAULT_DIR}/SynthRAD Viewer.app"

PY=$(command -v python3.12 || command -v python3.13 || command -v python3.11 || true)
[ -n "$PY" ] || { echo "Python 3.11+ not found. Install it with: brew install python@3.12"; exit 1; }
[ -x "$REPO/.venv/bin/python" ] || "$PY" -m venv "$REPO/.venv"
"$REPO/.venv/bin/pip" install -q --upgrade pip
"$REPO/.venv/bin/pip" install -q -e "$REPO"

rm -rf "$APP"
mkdir -p "$APP/Contents/MacOS" "$APP/Contents/Resources"
ICONSET="$(mktemp -d)/AppIcon.iconset"
"$REPO/.venv/bin/python" -m synthrad_viewer.icon "$ICONSET"
iconutil -c icns "$ICONSET" -o "$APP/Contents/Resources/AppIcon.icns"

# Run a copy of the framework's GUI interpreter from inside this bundle, so macOS shows
# "SynthRAD Viewer" (Dock, ⌘-Tab, Activity Monitor) instead of "Python". The venv is
# selected with __PYVENV_LAUNCHER__, the same variable Python's own launcher sets.
GUIPY="$("$REPO/.venv/bin/python" -c 'import sys; print(sys.base_prefix)')/Resources/Python.app/Contents/MacOS/Python"
if [ -x "$GUIPY" ]; then
  cp "$GUIPY" "$APP/Contents/MacOS/SynthRAD Viewer"
fi

cat > "$APP/Contents/MacOS/launcher" <<SH
#!/bin/bash
HERE="\$(cd "\$(dirname "\$0")" && pwd)"
export __PYVENV_LAUNCHER__="$REPO/.venv/bin/python"
# After a Python upgrade the bundled copy can no longer find its framework; fall back.
if "\$HERE/SynthRAD Viewer" -c "" 2>/dev/null; then
  exec "\$HERE/SynthRAD Viewer" -m synthrad_viewer "\$@"
fi
unset __PYVENV_LAUNCHER__
exec "$REPO/.venv/bin/python" -m synthrad_viewer "\$@"
SH
chmod +x "$APP/Contents/MacOS/launcher"

cat > "$APP/Contents/Info.plist" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>CFBundleName</key><string>SynthRAD Viewer</string>
  <key>CFBundleDisplayName</key><string>SynthRAD Viewer</string>
  <key>CFBundleIdentifier</key><string>org.synthrad.viewer</string>
  <key>CFBundleExecutable</key><string>launcher</string>
  <key>CFBundleIconFile</key><string>AppIcon</string>
  <key>CFBundlePackageType</key><string>APPL</string>
  <key>CFBundleShortVersionString</key><string>0.2.0</string>
  <key>NSHighResolutionCapable</key><true/>
  <key>NSRequiresAquaSystemAppearance</key><false/>
</dict>
</plist>
PLIST
touch "$APP"
echo "Installed: $APP"
