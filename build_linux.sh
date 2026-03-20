#!/usr/bin/env bash
# ═══════════════════════════════════════════════════════════════════════════
#  build_linux.sh  —  Builds devboard-linux.deb
#  Run this ON a Debian/Ubuntu Linux machine (amd64 or arm64).
#
#  Requirements:
#    pip3 install pyinstaller
#    sudo apt-get install -y dpkg-dev fakeroot   (usually pre-installed)
#
#  Output:  dist/devboard-linux.deb
# ═══════════════════════════════════════════════════════════════════════════
set -e

VERSION="10.0"
ARCH=$(dpkg --print-architecture 2>/dev/null || uname -m | sed 's/x86_64/amd64/;s/aarch64/arm64/')
PKG_NAME="devboard"
DEB_NAME="${PKG_NAME}-linux.deb"

echo
echo "───────────────────────────────────────────────────────────"
echo "  DevBoard — Linux .deb Build"
echo "  Version : ${VERSION}"
echo "  Arch    : ${ARCH}"
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
rm -rf build dist/__pycache__
rm -f  "dist/${DEB_NAME}" "dist/devboard"

# ── 4. PyInstaller ───────────────────────────────────────────
echo "  Running PyInstaller…"
pyinstaller devboard.spec --noconfirm

if [ ! -f "dist/devboard" ]; then
    echo "  ERROR: dist/devboard not found after PyInstaller run."
    exit 1
fi

# ── 5. Build .deb package tree ───────────────────────────────
echo "  Building .deb package tree…"
DEB_ROOT="dist/deb_pkg"
BIN_DIR="${DEB_ROOT}/usr/local/bin"
DOC_DIR="${DEB_ROOT}/usr/share/doc/${PKG_NAME}"

rm -rf  "${DEB_ROOT}"
mkdir -p "${BIN_DIR}" "${DOC_DIR}"
mkdir -p "${DEB_ROOT}/DEBIAN"

# Copy the binary
cp "dist/devboard" "${BIN_DIR}/devboard"
chmod 755 "${BIN_DIR}/devboard"

# Minimal README
cat > "${DOC_DIR}/README" <<'DOC'
DevBoard v10.0 — cross-platform system monitor.
Run: devboard
First run opens the setup wizard in your browser.
Docs: https://DA-COMPUTER.github.io/devboard
DOC

# ── 6. DEBIAN/control ────────────────────────────────────────
cat > "${DEB_ROOT}/DEBIAN/control" <<CTRL
Package: ${PKG_NAME}
Version: ${VERSION}
Section: utils
Priority: optional
Architecture: ${ARCH}
Maintainer: Your Name <you@example.com>
Description: DevBoard — cross-platform system monitor
 A self-hosted, browser-based dashboard providing live system stats,
 a terminal runner, file browser, Docker management, and more.
 Supports Linux, macOS, and Windows.
Homepage: https://DA-COMPUTER.github.io/devboard
CTRL

# ── 7. DEBIAN/postinst ───────────────────────────────────────
cat > "${DEB_ROOT}/DEBIAN/postinst" <<'POSTINST'
#!/bin/sh
set -e
echo ""
echo "  DevBoard installed to /usr/local/bin/devboard"
echo "  Run:  devboard"
echo "  The setup wizard will open in your browser on first run."
echo ""
POSTINST
chmod 755 "${DEB_ROOT}/DEBIAN/postinst"

# ── 8. DEBIAN/prerm (clean up service on uninstall) ──────────
cat > "${DEB_ROOT}/DEBIAN/prerm" <<'PRERM'
#!/bin/sh
set -e
# Stop the user service if it exists
systemctl --user stop   devboard 2>/dev/null || true
systemctl --user disable devboard 2>/dev/null || true
PRERM
chmod 755 "${DEB_ROOT}/DEBIAN/prerm"

# ── 9. Build the .deb ────────────────────────────────────────
echo "  Building .deb…"
fakeroot dpkg-deb --build "${DEB_ROOT}" "dist/${DEB_NAME}"

# ── 10. Size report ──────────────────────────────────────────
SIZE=$(du -sh "dist/${DEB_NAME}" | cut -f1)
echo
echo "  ✓  dist/${DEB_NAME}  (${SIZE})"
echo
echo "───────────────────────────────────────────────────────────"
echo "  Build complete."
echo "  Install locally:  sudo dpkg -i dist/${DEB_NAME}"
echo "  Upload dist/${DEB_NAME} to the GitHub Release."
echo "───────────────────────────────────────────────────────────"
echo
