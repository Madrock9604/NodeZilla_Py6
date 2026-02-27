#!/usr/bin/env bash
set -euo pipefail

# Build NodeZilla for Windows (10/11) from Git Bash.
#
# Outputs are written outside the repo:
#   - Portable zip
#   - Inno Setup installer (.exe), when ISCC.exe is available
#
# Usage:
#   scripts/build_windows_release.sh
#   NODEZILLA_PYTHON=python scripts/build_windows_release.sh
#   RELEASE_ROOT="$HOME/Documents/NodeZilla_Releases_Windows" scripts/build_windows_release.sh
#   APP_VERSION=1.0.0 scripts/build_windows_release.sh

ROOT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
PYTHON_BIN="${NODEZILLA_PYTHON:-python}"
RELEASE_ROOT="${RELEASE_ROOT:-$HOME/Documents/NodeZilla_Releases_Windows}"
APP_NAME="NodeZilla"
APP_VERSION="${APP_VERSION:-1.0.0}"

BUILD_DIR="$RELEASE_ROOT/pyi_build"
DIST_DIR="$RELEASE_ROOT/pyi_dist"
SPEC_DIR="$RELEASE_ROOT/pyi_spec"
PKG_DIR="$RELEASE_ROOT/release"
STAGE_DIR="$RELEASE_ROOT/stage"
APP_STAGE_DIR="$STAGE_DIR/$APP_NAME"
ZIP_PATH="$PKG_DIR/${APP_NAME}-Windows.zip"
ISS_PATH="$RELEASE_ROOT/${APP_NAME}_installer.iss"
INSTALLER_PATH="$PKG_DIR/${APP_NAME}-Windows-Setup.exe"

mkdir -p "$BUILD_DIR" "$DIST_DIR" "$SPEC_DIR" "$PKG_DIR" "$STAGE_DIR"

find_iscc() {
  local candidates=(
    "/c/Program Files (x86)/Inno Setup 6/ISCC.exe"
    "/c/Program Files/Inno Setup 6/ISCC.exe"
  )
  local p
  for p in "${candidates[@]}"; do
    [[ -f "$p" ]] && { echo "$p"; return 0; }
  done
  return 1
}

echo "[1/4] Building Windows app with PyInstaller..."
"$PYTHON_BIN" -m PyInstaller \
  --noconfirm --clean --windowed \
  --name "$APP_NAME" \
  --collect-all PySide6 \
  --add-data "$ROOT_DIR/assets;assets" \
  --add-data "$ROOT_DIR/Examples;Examples" \
  --add-data "$ROOT_DIR/PL.txt;." \
  --workpath "$BUILD_DIR" \
  --distpath "$DIST_DIR" \
  --specpath "$SPEC_DIR" \
  "$ROOT_DIR/run.py"

echo "[2/4] Staging app files..."
rm -rf "$APP_STAGE_DIR"
cp -R "$DIST_DIR/$APP_NAME" "$APP_STAGE_DIR"

cat > "$PKG_DIR/README_FIRST_RUN.txt" <<'EOF'
NodeZilla first-run behavior:
- On first launch, NodeZilla creates a user workspace at:
  %USERPROFILE%\Documents\NodeZilla
- User-editable files are copied there:
  - PL.txt
  - Examples\
  - assets\components\library\
  - assets\symbols\
  - assets\chips\

This keeps PL, Library, and Examples accessible outside the app install folder.
EOF

echo "[3/4] Creating portable zip..."
rm -f "$ZIP_PATH"
powershell.exe -NoProfile -Command \
  "Compress-Archive -Path '$APP_STAGE_DIR','${PKG_DIR}/README_FIRST_RUN.txt' -DestinationPath '$ZIP_PATH' -Force" >/dev/null

echo "[4/4] Building installer with Inno Setup..."
ISCC_EXE="$(find_iscc || true)"
if [[ -z "${ISCC_EXE}" ]]; then
  echo "WARNING: Inno Setup not found. Skipping installer build."
  echo "Install Inno Setup 6 and rerun to generate ${APP_NAME}-Windows-Setup.exe"
  echo
  echo "Build complete (zip only)."
  echo "Zip: $ZIP_PATH"
  exit 0
fi

APP_STAGE_DIR_WIN="$(cygpath -w "$APP_STAGE_DIR")"
PKG_DIR_WIN="$(cygpath -w "$PKG_DIR")"
INSTALLER_PATH_WIN="$(cygpath -w "$INSTALLER_PATH")"

cat > "$ISS_PATH" <<EOF
#define AppName "${APP_NAME}"
#define AppVersion "${APP_VERSION}"
#define AppPublisher "NodeZilla"
#define AppExeName "NodeZilla.exe"
#define AppSource "${APP_STAGE_DIR_WIN}"

[Setup]
AppId={{A5D36E8F-B7A4-4A3A-BD3D-5E613B5C4401}
AppName={#AppName}
AppVersion={#AppVersion}
AppPublisher={#AppPublisher}
DefaultDirName={autopf}\\{#AppName}
DefaultGroupName={#AppName}
DisableProgramGroupPage=yes
OutputDir=${PKG_DIR_WIN}
OutputBaseFilename=${APP_NAME}-Windows-Setup
Compression=lzma
SolidCompression=yes
WizardStyle=modern
ArchitecturesInstallIn64BitMode=x64compatible

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "Create a &desktop shortcut"; GroupDescription: "Additional icons:"

[Files]
Source: "{#AppSource}\\*"; DestDir: "{app}"; Flags: recursesubdirs createallsubdirs ignoreversion

[Icons]
Name: "{autoprograms}\\{#AppName}"; Filename: "{app}\\{#AppExeName}"
Name: "{autodesktop}\\{#AppName}"; Filename: "{app}\\{#AppExeName}"; Tasks: desktopicon

[Run]
Filename: "{app}\\{#AppExeName}"; Description: "Launch {#AppName}"; Flags: nowait postinstall skipifsilent
EOF

"$ISCC_EXE" "$ISS_PATH" >/dev/null

GENERATED_INSTALLER="$PKG_DIR/${APP_NAME}-Windows-Setup.exe"
if [[ -f "$GENERATED_INSTALLER" && "$GENERATED_INSTALLER" != "$INSTALLER_PATH" ]]; then
  mv -f "$GENERATED_INSTALLER" "$INSTALLER_PATH"
fi

echo
echo "Build complete."
echo "Portable zip: $ZIP_PATH"
echo "Installer:    $INSTALLER_PATH"
