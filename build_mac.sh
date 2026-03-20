#!/usr/bin/env bash
# ═══════════════════════════════════════════════════════════════════════════
#  build_mac.sh  —  Builds devboard-mac.dmg
#  Run this ON a macOS machine (Apple Silicon or Intel).
#
#  Requirements:
#    pip3 install pyinstaller
#    hdiutil  (ships with macOS — no install needed)
#
#  Output:  dist/devboard-mac.dmg
# ═══════════════════════════════════════════════════════════════════════════
set -e

VERSION="10.0"
APP_NAME="DevBoard"
BUNDLE_ID="com.devboard.app"
DMG_NAME="devboard-mac.dmg"

echo
echo "───────────────────────────────────────────────────────────"
echo "  DevBoard — macOS .dmg Build"
echo "  Version : ${VERSION}"
echo "  Arch    : $(uname -m)"
echo "───────────────────────────────────────────────────────────"
echo

# ── 1. Python check ──────────────────────────────────────────
python3 --version
echo "  Python path: $(which python3)"

# ── 2. Install build dependencies ────────────────────────────
echo "  Installing PyInstaller…"
pip3 install --quiet --upgrade pyinstaller

echo "  Installing app dependencies…"
pip3 install --quiet --upgrade flask psutil pywebview || \
    echo "  WARNING: Some deps failed (may be ok if already present)"

# ── 3. Clean ─────────────────────────────────────────────────
rm -rf build "dist/${APP_NAME}.app" "dist/${DMG_NAME}"

# ── 4. PyInstaller → .app bundle ─────────────────────────────
echo "  Running PyInstaller…"
pyinstaller devboard.spec --noconfirm

APP_PATH="dist/${APP_NAME}.app"
if [ ! -d "${APP_PATH}" ]; then
    echo "  ERROR: ${APP_PATH} not found after PyInstaller run."
    exit 1
fi

# ── 5. Build .dmg with hdiutil ───────────────────────────────
echo "  Creating .dmg with hdiutil…"
DMG_STAGE="dist/dmg_stage"
rm -rf "${DMG_STAGE}"
mkdir -p "${DMG_STAGE}"

# Copy the .app bundle into the staging dir
cp -R "${APP_PATH}" "${DMG_STAGE}/"

# Create a symlink to /Applications so users can drag & drop
ln -s /Applications "${DMG_STAGE}/Applications"

# Optional: copy a background image if present
if [ -f "assets/dmg_background.png" ]; then
    mkdir -p "${DMG_STAGE}/.background"
    cp "assets/dmg_background.png" "${DMG_STAGE}/.background/background.png"
fi

# Create a writable temp DMG, then convert to compressed read-only
TEMP_DMG="dist/devboard-temp.dmg"
hdiutil create \
    -volname "${APP_NAME}" \
    -srcfolder "${DMG_STAGE}" \
    -ov \
    -format UDRW \
    "${TEMP_DMG}"

# Mount it to set icon positions (optional visual polish)
MOUNT_POINT=$(hdiutil attach "${TEMP_DMG}" -readwrite -noverify | \
    grep "Volumes" | awk '{print $NF}')

if [ -n "${MOUNT_POINT}" ]; then
    # Brief pause to let Finder register
    sleep 1

    # Set window properties via AppleScript
    osascript <<APPLESCRIPT || true
tell application "Finder"
    tell disk "${APP_NAME}"
        open
        set current view of container window to icon view
        set toolbar visible of container window to false
        set statusbar visible of container window to false
        set the bounds of container window to {100, 100, 700, 480}
        set viewOptions to the icon view options of container window
        set arrangement of viewOptions to not arranged
        set icon size of viewOptions to 96
        set position of item "${APP_NAME}.app" of container window to {160, 180}
        set position of item "Applications" of container window to {440, 180}
        close
        open
        update without registering applications
    end tell
end tell
APPLESCRIPT

    hdiutil detach "${MOUNT_POINT}" -quiet || true
fi

# Convert to compressed, read-only DMG
hdiutil convert "${TEMP_DMG}" \
    -format UDZO \
    -imagekey zlib-level=9 \
    -o "dist/${DMG_NAME}"

rm -f "${TEMP_DMG}"
rm -rf "${DMG_STAGE}"

# ── 6. Size report ───────────────────────────────────────────
SIZE=$(du -sh "dist/${DMG_NAME}" | cut -f1)
echo
echo "  ✓  dist/${DMG_NAME}  (${SIZE})"
echo
echo "───────────────────────────────────────────────────────────"
echo "  Build complete."
echo ""
echo "  ⚠  NOTE on Gatekeeper:"
echo "     The .app is unsigned. Users must right-click → Open"
echo "     on first launch, or run:"
echo "     xattr -dr com.apple.quarantine '/Applications/${APP_NAME}.app'"
echo ""
echo "  Upload dist/${DMG_NAME} to the GitHub Release."
echo "───────────────────────────────────────────────────────────"
echo
