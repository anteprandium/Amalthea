#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT_DIR"

SAGE_BIN="${SAGE_BIN:-/usr/local/bin/sage}"
PYTHON_VERSION="${PYTHON_VERSION:-3.12}"
PYTHON_BUILD_DIR="$ROOT_DIR/.python-build"
PYTHON_BIN="${PYTHON_BIN:-$PYTHON_BUILD_DIR/python/bin/python3}"
PYTHON_BUILD_URL="${PYTHON_BUILD_URL:-https://github.com/astral-sh/python-build-standalone/releases/download/20251120/cpython-3.12.12%2B20251120-aarch64-apple-darwin-install_only.tar.gz}"

if [[ ! -x "$SAGE_BIN" ]]; then
  echo "SageMath was not found at: $SAGE_BIN" >&2
  echo "Set SAGE_BIN=/path/to/sage and retry." >&2
  exit 1
fi

if [[ -x "$PYTHON_BIN" ]]; then
  BUILD_PYTHON_VERSION="$("$PYTHON_BIN" -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")')"
else
  BUILD_PYTHON_VERSION=""
fi

if [[ "$BUILD_PYTHON_VERSION" != "$PYTHON_VERSION" ]]; then
  if [[ "$(uname -s)" != "Darwin" || "$(uname -m)" != "arm64" ]]; then
    echo "No build Python found at: $PYTHON_BIN" >&2
    echo "Set PYTHON_BIN=/path/to/python3 for this platform." >&2
    exit 1
  fi

  echo "Downloading standalone CPython $PYTHON_VERSION..."
  rm -rf "$PYTHON_BUILD_DIR"
  mkdir -p "$PYTHON_BUILD_DIR"
  curl -L "$PYTHON_BUILD_URL" | tar -xz -C "$PYTHON_BUILD_DIR"
fi

if [[ -x ".venv/bin/python" ]]; then
  VENV_VERSION="$(".venv/bin/python" -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")')"
else
  VENV_VERSION=""
fi

if [[ "$VENV_VERSION" != "$PYTHON_VERSION" ]]; then
  echo "Recreating .venv with $PYTHON_BIN..."
  rm -rf .venv
  "$PYTHON_BIN" -m venv .venv
fi

if ! .venv/bin/python -c "import pip" >/dev/null 2>&1; then
  echo "Bootstrapping pip in .venv..."
  .venv/bin/python -m ensurepip --upgrade
fi

echo "Installing build requirements..."
.venv/bin/python -m pip install --upgrade pip setuptools wheel
.venv/bin/python -m pip install -r requirements.txt

if [[ "${CLEAN:-1}" == "1" ]]; then
  echo "Removing previous local build outputs..."
  rm -rf build dist
fi

echo "Building Amalthea.app with PyInstaller..."
.venv/bin/pyinstaller \
  --noconfirm \
  --clean \
  --windowed \
  --name Amalthea \
  --icon appIcon.icns \
  --add-data "appIcon.icns:." \
  --add-data "docIcon.icns:." \
  --hidden-import objc \
  --hidden-import AppKit \
  --hidden-import Foundation \
  --hidden-import WebKit \
  Amalthea.py

rm -f Amalthea.spec

APP_BUNDLE="$ROOT_DIR/dist/Amalthea.app"
INFO_PLIST="$APP_BUNDLE/Contents/Info.plist"
RESOURCES_DIR="$APP_BUNDLE/Contents/Resources"
CONTENTS_DIR="$APP_BUNDLE/Contents"

if [[ ! -d "$APP_BUNDLE" ]]; then
  echo "Build failed: $APP_BUNDLE was not created." >&2
  exit 1
fi

echo "Normalising app metadata..."
/bin/mkdir -p "$RESOURCES_DIR"
/bin/cp appIcon.icns "$RESOURCES_DIR/appIcon.icns"
/bin/cp docIcon.icns "$RESOURCES_DIR/docIcon.icns"
/usr/libexec/PlistBuddy -c "Set :LSMinimumSystemVersion 13.0" "$INFO_PLIST" 2>/dev/null \
  || /usr/libexec/PlistBuddy -c "Add :LSMinimumSystemVersion string 13.0" "$INFO_PLIST"
/usr/libexec/PlistBuddy -c "Set :CFBundleIconFile appIcon.icns" "$INFO_PLIST" 2>/dev/null \
  || /usr/libexec/PlistBuddy -c "Add :CFBundleIconFile string appIcon.icns" "$INFO_PLIST"
/usr/libexec/PlistBuddy -c "Set :CFBundleIdentifier com.anteprandium.amalthea" "$INFO_PLIST" 2>/dev/null \
  || /usr/libexec/PlistBuddy -c "Add :CFBundleIdentifier string com.anteprandium.amalthea" "$INFO_PLIST"
/usr/libexec/PlistBuddy -c "Set :CFBundleShortVersionString 0.9.0" "$INFO_PLIST" 2>/dev/null \
  || /usr/libexec/PlistBuddy -c "Add :CFBundleShortVersionString string 0.9.0" "$INFO_PLIST"
/usr/libexec/PlistBuddy -c "Set :CFBundleVersion 0.9.0" "$INFO_PLIST" 2>/dev/null \
  || /usr/libexec/PlistBuddy -c "Add :CFBundleVersion string 0.9.0" "$INFO_PLIST"
/usr/libexec/PlistBuddy -c "Set :CFBundleGetInfoString A stand-alone Jupyter notebook for SageMath" "$INFO_PLIST" 2>/dev/null \
  || /usr/libexec/PlistBuddy -c "Add :CFBundleGetInfoString string A stand-alone Jupyter notebook for SageMath" "$INFO_PLIST"
/usr/libexec/PlistBuddy -c "Set :NSHumanReadableCopyright CC0 2020, 2021, 2025, 2026, Anteprandium" "$INFO_PLIST" 2>/dev/null \
  || /usr/libexec/PlistBuddy -c "Add :NSHumanReadableCopyright string CC0 2020, 2021, 2025, 2026, Anteprandium" "$INFO_PLIST"
/usr/libexec/PlistBuddy -c "Delete :CFBundleDocumentTypes" "$INFO_PLIST" 2>/dev/null || true
/usr/libexec/PlistBuddy -c "Add :CFBundleDocumentTypes array" "$INFO_PLIST"
/usr/libexec/PlistBuddy -c "Add :CFBundleDocumentTypes:0 dict" "$INFO_PLIST"
/usr/libexec/PlistBuddy -c "Add :CFBundleDocumentTypes:0:CFBundleTypeExtensions array" "$INFO_PLIST"
/usr/libexec/PlistBuddy -c "Add :CFBundleDocumentTypes:0:CFBundleTypeExtensions:0 string ipynb" "$INFO_PLIST"
/usr/libexec/PlistBuddy -c "Add :CFBundleDocumentTypes:0:CFBundleTypeIconFile string docIcon.icns" "$INFO_PLIST"
/usr/libexec/PlistBuddy -c "Add :CFBundleDocumentTypes:0:CFBundleTypeMIMETypes array" "$INFO_PLIST"
/usr/libexec/PlistBuddy -c "Add :CFBundleDocumentTypes:0:CFBundleTypeMIMETypes:0 string application/x-ipynb+json" "$INFO_PLIST"
/usr/libexec/PlistBuddy -c "Add :CFBundleDocumentTypes:0:CFBundleTypeName string Jupyter Notebook" "$INFO_PLIST"
/usr/libexec/PlistBuddy -c "Add :CFBundleDocumentTypes:0:CFBundleTypeRole string Editor" "$INFO_PLIST"

echo "Removing stale code signature..."
rm -rf "$APP_BUNDLE/Contents/_CodeSignature"

echo "Signing app bundle..."
codesign --force --deep --sign - "$APP_BUNDLE"

echo "Clearing quarantine attributes..."
xattr -dr com.apple.quarantine "$APP_BUNDLE" 2>/dev/null || true

echo "Built: $APP_BUNDLE"
