#!/usr/bin/env bash
set -euo pipefail

# Build NodeZilla macOS app with PyInstaller while keeping build artifacts
# outside the git repo.
#
# Usage:
#   scripts/build_mac_release.sh
#   NODEZILLA_PYTHON=/opt/anaconda3/envs/nodezilla/bin/python scripts/build_mac_release.sh
#   RELEASE_ROOT="$HOME/Documents/NodeZilla_Releases" scripts/build_mac_release.sh
#   DWF_BUNDLE_MODE=off scripts/build_mac_release.sh
#   DWF_BUNDLE_MODE=/custom/path/to/libdwf.dylib scripts/build_mac_release.sh

ROOT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
PYTHON_BIN="${NODEZILLA_PYTHON:-/opt/anaconda3/envs/nodezilla/bin/python}"
RELEASE_ROOT="${RELEASE_ROOT:-$HOME/Documents/NodeZilla_Releases}"
APP_NAME="NodeZilla"
APP_VERSION="${APP_VERSION:-1.0.0}"
DWF_BUNDLE_MODE="${DWF_BUNDLE_MODE:-auto}"  # auto | off | /absolute/path
SIGN_IDENTITY="${SIGN_IDENTITY:--}"         # "-" = ad-hoc sign

BUILD_DIR="$RELEASE_ROOT/pyi_build"
DIST_DIR="$RELEASE_ROOT/pyi_dist"
SPEC_DIR="$RELEASE_ROOT/pyi_spec"
PKG_DIR="$RELEASE_ROOT/release"
ZIP_PATH="$PKG_DIR/${APP_NAME}-macOS.zip"
INSTALLER_STAGE="$RELEASE_ROOT/pkg_stage"
INSTALLER_PATH="$PKG_DIR/${APP_NAME}-macOS.pkg"
INSTALLER_SCRIPTS="$RELEASE_ROOT/pkg_scripts"

mkdir -p "$BUILD_DIR" "$DIST_DIR" "$SPEC_DIR" "$PKG_DIR"

DWF_ADD_BINARY_ARGS=()
if [[ "$DWF_BUNDLE_MODE" != "off" ]]; then
  DWF_CANDIDATE=""
  if [[ "$DWF_BUNDLE_MODE" != "auto" ]]; then
    DWF_CANDIDATE="$DWF_BUNDLE_MODE"
  else
    for c in \
      "/Library/Frameworks/dwf.framework/dwf" \
      "/usr/local/lib/libdwf.dylib"
    do
      if [[ -f "$c" ]]; then
        DWF_CANDIDATE="$c"
        break
      fi
    done
  fi
  if [[ -n "$DWF_CANDIDATE" && -f "$DWF_CANDIDATE" ]]; then
    echo "Including DWF runtime: $DWF_CANDIDATE"
    # Bundle the runtime in both locations:
    # - Frameworks/dwf      (satisfies @rpath/dwf)
    # - Frameworks/dwf.framework/dwf (framework-style path probe)
    DWF_ADD_BINARY_ARGS=(
      --add-binary "$DWF_CANDIDATE:."
      --add-binary "$DWF_CANDIDATE:dwf.framework"
    )
  else
    echo "DWF runtime not bundled (not found). Users need WaveForms runtime installed."
  fi
fi

echo "[1/4] Building macOS app..."
"$PYTHON_BIN" -m PyInstaller \
  --noconfirm --clean --windowed \
  --name "$APP_NAME" \
  --collect-all PySide6 \
  "${DWF_ADD_BINARY_ARGS[@]}" \
  --add-data "$ROOT_DIR/assets:assets" \
  --add-data "$ROOT_DIR/Examples:Examples" \
  --add-data "$ROOT_DIR/PL.txt:." \
  --workpath "$BUILD_DIR" \
  --distpath "$DIST_DIR" \
  --specpath "$SPEC_DIR" \
  "$ROOT_DIR/run.py"

echo "[2/4] Packaging app..."
rm -rf "$PKG_DIR/$APP_NAME.app"
cp -R "$DIST_DIR/$APP_NAME.app" "$PKG_DIR/$APP_NAME.app"

echo "[2.2/4] Verifying bundled DWF runtime dependencies..."
DWF_MAIN=""
if [[ -f "$PKG_DIR/$APP_NAME.app/Contents/Frameworks/dwf" ]]; then
  DWF_MAIN="$PKG_DIR/$APP_NAME.app/Contents/Frameworks/dwf"
elif [[ -f "$PKG_DIR/$APP_NAME.app/Contents/Frameworks/dwf.framework/dwf" ]]; then
  DWF_MAIN="$PKG_DIR/$APP_NAME.app/Contents/Frameworks/dwf.framework/dwf"
fi

if [[ -n "$DWF_MAIN" ]]; then
  DWF_RPATH_DEPS=$(
    /usr/bin/otool -L "$DWF_MAIN" \
      | /usr/bin/awk '/@rpath\//{gsub("@rpath/","",$1); print $1}'
  )
  while IFS= read -r dep; do
    [[ -z "$dep" ]] && continue
    [[ "$dep" == "dwf" ]] && continue
    TARGET_DEP="$PKG_DIR/$APP_NAME.app/Contents/Frameworks/$dep"
    if [[ -f "$TARGET_DEP" ]]; then
      continue
    fi
    FOUND_DEP=""
    for cand in \
      "/usr/local/lib/$dep" \
      "/opt/homebrew/lib/$dep" \
      "/Library/Frameworks/dwf.framework/Versions/A/$dep" \
      "/Library/Frameworks/dwf.framework/$dep"
    do
      if [[ -f "$cand" ]]; then
        FOUND_DEP="$cand"
        break
      fi
    done
    if [[ -n "$FOUND_DEP" ]]; then
      echo "Adding missing DWF dependency: $dep"
      /bin/cp -f "$FOUND_DEP" "$TARGET_DEP"
    else
      echo "ERROR: missing DWF dependency '$dep' (not found on build machine)." >&2
      exit 1
    fi
  done <<< "$DWF_RPATH_DEPS"
fi

echo "[2.5/4] Signing app bundle..."
/usr/bin/codesign --force --deep --sign "$SIGN_IDENTITY" "$PKG_DIR/$APP_NAME.app"

cat > "$PKG_DIR/README_FIRST_RUN.txt" <<'EOF'
NodeZilla first-run behavior:
- On first launch, NodeZilla creates a user workspace at:
  ~/Documents/NodeZilla
- User-editable files are copied there:
  - PL.txt
  - Examples/
  - assets/components/library/
  - assets/symbols/
  - assets/chips/

This keeps PL, Library, and Examples accessible and editable outside the app bundle.
EOF

echo "[3/4] Creating zip..."
rm -f "$ZIP_PATH"
(cd "$PKG_DIR" && /usr/bin/zip -r "$ZIP_PATH" "$APP_NAME.app" "README_FIRST_RUN.txt" >/dev/null)

echo "[4/4] Creating macOS installer (.pkg)..."
rm -rf "$INSTALLER_STAGE"
rm -rf "$INSTALLER_SCRIPTS"
mkdir -p "$INSTALLER_STAGE/Applications"
mkdir -p "$INSTALLER_SCRIPTS"
cp -R "$PKG_DIR/$APP_NAME.app" "$INSTALLER_STAGE/Applications/$APP_NAME.app"

cat > "$INSTALLER_SCRIPTS/postinstall" <<'EOF'
#!/bin/bash
set -euo pipefail

APP_PATH="/Applications/NodeZilla.app"
CONSOLE_USER="$(/usr/bin/stat -f%Su /dev/console || true)"

if [[ -z "${CONSOLE_USER}" || "${CONSOLE_USER}" == "root" || "${CONSOLE_USER}" == "loginwindow" ]]; then
  exit 0
fi

USER_HOME="$(/usr/bin/dscl . -read "/Users/${CONSOLE_USER}" NFSHomeDirectory 2>/dev/null | /usr/bin/awk '{print $2}' || true)"
if [[ -z "${USER_HOME}" ]]; then
  USER_HOME="/Users/${CONSOLE_USER}"
fi

TARGET_ROOT="${USER_HOME}/Documents/NodeZilla"
mkdir -p "${TARGET_ROOT}/Examples"
mkdir -p "${TARGET_ROOT}/assets/components/library"
mkdir -p "${TARGET_ROOT}/assets/symbols"
mkdir -p "${TARGET_ROOT}/assets/chips"
mkdir -p "${TARGET_ROOT}/Projects"

copy_missing_tree() {
  local src="$1"
  local dst="$2"
  if [[ ! -d "$src" ]]; then
    return 0
  fi
  /usr/bin/rsync -a --ignore-existing "$src"/ "$dst"/
}

copy_first_file_if_missing() {
  local dst="$1"
  shift
  if [[ -f "$dst" ]]; then
    return 0
  fi
  for src in "$@"; do
    if [[ -f "$src" ]]; then
      /bin/cp -f "$src" "$dst"
      return 0
    fi
  done
}

copy_first_dir_if_exists() {
  local dst="$1"
  shift
  for src in "$@"; do
    if [[ -d "$src" ]]; then
      copy_missing_tree "$src" "$dst"
      return 0
    fi
  done
}

copy_first_dir_if_exists "${TARGET_ROOT}/Examples" \
  "${APP_PATH}/Contents/MacOS/Examples" \
  "${APP_PATH}/Contents/Resources/Examples" \
  "${APP_PATH}/Contents/Frameworks/Examples"

copy_first_dir_if_exists "${TARGET_ROOT}/assets/components/library" \
  "${APP_PATH}/Contents/MacOS/assets/components/library" \
  "${APP_PATH}/Contents/Resources/assets/components/library" \
  "${APP_PATH}/Contents/Frameworks/assets/components/library"

copy_first_dir_if_exists "${TARGET_ROOT}/assets/symbols" \
  "${APP_PATH}/Contents/MacOS/assets/symbols" \
  "${APP_PATH}/Contents/Resources/assets/symbols" \
  "${APP_PATH}/Contents/Frameworks/assets/symbols"

copy_first_dir_if_exists "${TARGET_ROOT}/assets/chips" \
  "${APP_PATH}/Contents/MacOS/assets/chips" \
  "${APP_PATH}/Contents/Resources/assets/chips" \
  "${APP_PATH}/Contents/Frameworks/assets/chips"

copy_first_file_if_missing "${TARGET_ROOT}/PL.txt" \
  "${APP_PATH}/Contents/MacOS/PL.txt" \
  "${APP_PATH}/Contents/Resources/PL.txt" \
  "${APP_PATH}/Contents/Frameworks/PL.txt"

/usr/sbin/chown -R "${CONSOLE_USER}":staff "${TARGET_ROOT}" || true
exit 0
EOF
/bin/chmod +x "$INSTALLER_SCRIPTS/postinstall"

rm -f "$INSTALLER_PATH"
/usr/bin/pkgbuild \
  --root "$INSTALLER_STAGE" \
  --scripts "$INSTALLER_SCRIPTS" \
  --identifier "com.nodezilla.app" \
  --version "$APP_VERSION" \
  --install-location "/" \
  "$INSTALLER_PATH"

echo
echo "Build complete."
echo "App: $PKG_DIR/$APP_NAME.app"
echo "Zip: $ZIP_PATH"
echo "Installer: $INSTALLER_PATH"
