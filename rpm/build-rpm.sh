#!/usr/bin/env bash
# ═══════════════════════════════════════════════════════════════════════════════
# Wi-Vi Sentinel — RPM Build Script
# ═══════════════════════════════════════════════════════════════════════════════
#
# Builds the wivi-sentinel RPM package.
#
# Prerequisites (Fedora/RHEL):
#   sudo dnf install rpm-build rpmdevtools
#
# Usage:
#   cd wivi-sentinel/
#   ./rpm/build-rpm.sh
#
# Output: ~/rpmbuild/RPMS/noarch/wivi-sentinel-*.noarch.rpm
# ═══════════════════════════════════════════════════════════════════════════════

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"

NAME="wivi-sentinel"
VERSION="2.0.0"
TARBALL="${NAME}-${VERSION}"

RED='\033[0;31m'; GREEN='\033[0;32m'; CYAN='\033[0;36m'
BOLD='\033[1m'; NC='\033[0m'

log() { echo -e "${GREEN}[BUILD]${NC} $*"; }
err() { echo -e "${RED}[ERROR]${NC} $*"; exit 1; }

# ── Preflight ────────────────────────────────────────────────────────────────

if ! command -v rpmbuild &>/dev/null; then
    err "rpmbuild not found. Install with: sudo dnf install rpm-build rpmdevtools"
fi

# Check for pre-built dashboard
if [ ! -f "$PROJECT_DIR/dist/index.html" ]; then
    err "dist/index.html not found. Build the dashboard first: npm run build"
fi

log "Building ${NAME}-${VERSION} RPM"

# ── Set up rpmbuild tree ─────────────────────────────────────────────────────

rpmdev-setuptree 2>/dev/null || {
    mkdir -p ~/rpmbuild/{BUILD,RPMS,SOURCES,SPECS,SRPMS}
}

# ── Create source tarball ────────────────────────────────────────────────────

log "Creating source tarball..."

STAGING=$(mktemp -d)
STAGE_DIR="${STAGING}/${TARBALL}"
mkdir -p "$STAGE_DIR"

# Copy application files
cp "$PROJECT_DIR/server.py"          "$STAGE_DIR/"
cp "$PROJECT_DIR/requirements.txt"   "$STAGE_DIR/"
cp "$PROJECT_DIR/.env.example"       "$STAGE_DIR/"
cp "$PROJECT_DIR/start.sh"           "$STAGE_DIR/"

# Engine
mkdir -p "$STAGE_DIR/engine"
cp "$PROJECT_DIR"/engine/*.py        "$STAGE_DIR/engine/"

# Pre-built dashboard
cp -a "$PROJECT_DIR/dist"            "$STAGE_DIR/dist"

# RPM-specific files
mkdir -p "$STAGE_DIR/rpm"
cp "$SCRIPT_DIR/wivi-sentinel-setup"        "$STAGE_DIR/rpm/"
cp "$SCRIPT_DIR/wivi-sentinel.service"      "$STAGE_DIR/rpm/"
cp "$SCRIPT_DIR/wivi-sentinel-firewall.xml" "$STAGE_DIR/rpm/"

# Create tarball
tar czf ~/rpmbuild/SOURCES/${TARBALL}.tar.gz -C "$STAGING" "$TARBALL"
rm -rf "$STAGING"

log "Source tarball created"

# ── Copy spec file ───────────────────────────────────────────────────────────

cp "$SCRIPT_DIR/wivi-sentinel.spec" ~/rpmbuild/SPECS/

# ── Build RPM ────────────────────────────────────────────────────────────────

log "Building RPM (this may take a moment)..."

rpmbuild -bb ~/rpmbuild/SPECS/wivi-sentinel.spec 2>&1 | sed 's/^/  /'

# ── Find the output ──────────────────────────────────────────────────────────

RPM_FILE=$(find ~/rpmbuild/RPMS -name "${NAME}-${VERSION}*.rpm" -type f | head -1)

if [ -z "$RPM_FILE" ]; then
    err "RPM build failed — no output file found"
fi

# Copy to project directory for convenience
cp "$RPM_FILE" "$PROJECT_DIR/"
RPM_BASENAME=$(basename "$RPM_FILE")

echo ""
echo -e "${CYAN}═══════════════════════════════════════════════════════════════${NC}"
echo -e "${GREEN}  RPM built successfully!${NC}"
echo -e "${CYAN}═══════════════════════════════════════════════════════════════${NC}"
echo ""
echo -e "  ${BOLD}Package:${NC}  ${PROJECT_DIR}/${RPM_BASENAME}"
echo -e "  ${BOLD}Size:${NC}     $(du -h "$PROJECT_DIR/$RPM_BASENAME" | cut -f1)"
echo ""
echo -e "  ${BOLD}Install on target machine:${NC}"
echo -e "    scp ${RPM_BASENAME} user@device:~/"
echo -e "    ssh user@device"
echo -e "    sudo dnf install ./${RPM_BASENAME}"
echo -e "    sudo wivi-sentinel-setup"
echo ""
echo -e "${CYAN}═══════════════════════════════════════════════════════════════${NC}"
