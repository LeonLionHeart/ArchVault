#!/bin/bash
# ═══════════════════════════════════════════════════════════════════════════
#  ArchVault v5.0.0-beta — Build & Package Script
#  Creates a ready-to-upload AUR package and GitHub release tarball.
# ═══════════════════════════════════════════════════════════════════════════
set -euo pipefail

VERSION="5.0.0-beta"
PKGNAME="archvault"
TAG="v${VERSION}"
PKGVER="${VERSION//-/_}"       # AUR uses underscores: 5.0.0_beta

RED='\033[0;31m'; GREEN='\033[0;32m'; CYAN='\033[0;36m'
BOLD='\033[1m'; NC='\033[0m'

info()  { echo -e "${CYAN}${BOLD}[INFO]${NC}  $*"; }
ok()    { echo -e "${GREEN}${BOLD}[OK]${NC}    $*"; }
err()   { echo -e "${RED}${BOLD}[ERR]${NC}   $*" >&2; }

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
BUILD_DIR="${SCRIPT_DIR}/build"
RELEASE_DIR="${SCRIPT_DIR}/release"
SRC_DIR="${BUILD_DIR}/${PKGNAME}-${VERSION}"

# ── Clean ─────────────────────────────────────────────────────────────────
info "Cleaning previous builds…"
rm -rf "${BUILD_DIR}" "${RELEASE_DIR}"
mkdir -p "${SRC_DIR}" "${RELEASE_DIR}"

# ── Collect source files ──────────────────────────────────────────────────
info "Collecting source files…"

# Python source
PYFILES=(
    archvault.py
    core_backend.py
    core_engine.py
    engine_backup.py
    engine_base.py
    engine_cloud.py
    engine_restore.py
    soft_ui_components.py
    ui_shell.py
    ui_tab_backup.py
    ui_tab_dashboard.py
    ui_tab_jobs.py
    ui_tab_restore.py
    ui_tab_settings.py
    ui_tab_snapshot_browser.py
    ui_tab_tasks.py
    ui_tabs_main.py
    ui_tabs_targets.py
    ui_widgets.py
)

# Notification templates (if present)
NOTIF_FILES=(
    notif_tpl_backup_failed.py
    notif_tpl_backup_success.py
    notif_tpl_system_alert.py
)

missing=0
for f in "${PYFILES[@]}"; do
    if [[ -f "${SCRIPT_DIR}/${f}" ]]; then
        cp "${SCRIPT_DIR}/${f}" "${SRC_DIR}/"
    else
        err "Missing required file: ${f}"
        missing=1
    fi
done

for f in "${NOTIF_FILES[@]}"; do
    [[ -f "${SCRIPT_DIR}/${f}" ]] && cp "${SCRIPT_DIR}/${f}" "${SRC_DIR}/"
done

if [[ $missing -eq 1 ]]; then
    err "Cannot continue — missing source files."
    exit 1
fi

# Package assets
for asset in archvault.desktop archvault.svg LICENSE PKGBUILD; do
    if [[ -f "${SCRIPT_DIR}/${asset}" ]]; then
        cp "${SCRIPT_DIR}/${asset}" "${SRC_DIR}/"
    else
        err "Missing asset: ${asset}"
        exit 1
    fi
done

ok "Collected $(ls "${SRC_DIR}"/*.py | wc -l) Python files + assets"

# ── Verify versions match ─────────────────────────────────────────────────
info "Verifying version strings…"
BAD_VER=0
for f in "${SRC_DIR}"/*.py; do
    if grep -q 'VERSION = ' "$f"; then
        v=$(grep 'VERSION = ' "$f" | head -1 | sed 's/.*"\(.*\)".*/\1/')
        if [[ "$v" != "v${VERSION}" ]]; then
            err "  $(basename $f): VERSION=$v (expected v${VERSION})"
            BAD_VER=1
        fi
    fi
done
if [[ $BAD_VER -eq 1 ]]; then
    err "Version mismatch detected. Fix before releasing."
    exit 1
fi
ok "All version strings: v${VERSION}"

# ── Syntax check ──────────────────────────────────────────────────────────
info "Syntax checking all Python files…"
for f in "${SRC_DIR}"/*.py; do
    if ! python3 -c "import ast; ast.parse(open('${f}').read())" 2>/dev/null; then
        err "  Syntax error in $(basename $f)"
        python3 -c "import ast; ast.parse(open('${f}').read())"
        exit 1
    fi
done
ok "All $(ls "${SRC_DIR}"/*.py | wc -l) files pass syntax check"

# ── Create GitHub release tarball ─────────────────────────────────────────
info "Creating release tarball…"
TARBALL="${RELEASE_DIR}/${PKGNAME}-${VERSION}.tar.gz"
(cd "${BUILD_DIR}" && tar czf "${TARBALL}" "${PKGNAME}-${VERSION}/")
TARSIZE=$(du -h "${TARBALL}" | cut -f1)
ok "Tarball: ${TARBALL} (${TARSIZE})"

# ── Compute SHA256 ────────────────────────────────────────────────────────
SHA256=$(sha256sum "${TARBALL}" | cut -d' ' -f1)
info "SHA256: ${SHA256}"

# ── Generate PKGBUILD with correct checksum ───────────────────────────────
info "Generating PKGBUILD with checksum…"
AUR_DIR="${RELEASE_DIR}/aur-${PKGNAME}"
mkdir -p "${AUR_DIR}"
sed "s/sha256sums=('SKIP')/sha256sums=('${SHA256}')/" \
    "${SRC_DIR}/PKGBUILD" > "${AUR_DIR}/PKGBUILD"
ok "PKGBUILD written to ${AUR_DIR}/PKGBUILD"

# ── Generate .SRCINFO ─────────────────────────────────────────────────────
info "Generating .SRCINFO…"
cat > "${AUR_DIR}/.SRCINFO" <<EOF
pkgbase = ${PKGNAME}
	pkgdesc = Backup & Restore Manager for Arch Linux — GUI for rsync, btrfs, tar, rclone with scheduling, encryption, and cloud support
	pkgver = ${PKGVER}
	pkgrel = 1
	url = https://github.com/YOUR_USERNAME/archvault
	arch = any
	license = GPL3
	depends = python
	depends = python-pyqt6
	depends = python-cryptography
	depends = rsync
	depends = btrfs-progs
	depends = tar
	depends = gzip
	depends = coreutils
	depends = systemd
	optdepends = rclone: Cloud backup support (S3, GCS, Azure, Backblaze, Dropbox, Google Drive)
	optdepends = python-boto3: Direct AWS S3 cloud uploads
	optdepends = zstd: Zstandard compression for backups
	optdepends = cifs-utils: SMB/CIFS network share mounting
	optdepends = nfs-utils: NFS network share mounting
	optdepends = sshfs: SSHFS remote filesystem mounting
	optdepends = openssh: SFTP backup target support
	optdepends = libnotify: Desktop notification support
	source = ${PKGNAME}-${PKGVER}.tar.gz::https://github.com/YOUR_USERNAME/archvault/archive/refs/tags/${TAG}.tar.gz
	sha256sums = ${SHA256}

pkgname = ${PKGNAME}
EOF
ok ".SRCINFO generated"

# ── Test build (optional — requires makepkg) ──────────────────────────────
if command -v makepkg &>/dev/null; then
    echo ""
    read -p "$(echo -e "${CYAN}Run test build with makepkg? [y/N]: ${NC}")" TEST_BUILD
    if [[ "${TEST_BUILD,,}" == "y" ]]; then
        info "Running makepkg in ${AUR_DIR}…"
        # Copy tarball so makepkg can find it
        cp "${TARBALL}" "${AUR_DIR}/${PKGNAME}-${PKGVER}.tar.gz"
        (cd "${AUR_DIR}" && makepkg -sf --noconfirm)
        PKG_FILE=$(ls "${AUR_DIR}"/*.pkg.tar.zst 2>/dev/null | head -1)
        if [[ -n "${PKG_FILE}" ]]; then
            ok "Package built: ${PKG_FILE}"
            PKGSIZE=$(du -h "${PKG_FILE}" | cut -f1)
            info "Package size: ${PKGSIZE}"
            echo ""
            read -p "$(echo -e "${CYAN}Install locally with pacman? [y/N]: ${NC}")" INSTALL
            if [[ "${INSTALL,,}" == "y" ]]; then
                sudo pacman -U "${PKG_FILE}"
                ok "Installed! Run 'archvault' to launch."
            fi
        else
            err "makepkg did not produce a package."
        fi
    fi
else
    info "makepkg not found — skipping test build."
fi

# ── Summary ───────────────────────────────────────────────────────────────
echo ""
echo -e "${BOLD}═══════════════════════════════════════════════════════════${NC}"
echo -e "${GREEN}${BOLD}  ArchVault v${VERSION} — Build Complete${NC}"
echo -e "${BOLD}═══════════════════════════════════════════════════════════${NC}"
echo ""
echo -e "  ${BOLD}Release tarball:${NC}  ${TARBALL}"
echo -e "  ${BOLD}AUR directory:${NC}    ${AUR_DIR}/"
echo -e "  ${BOLD}SHA256:${NC}           ${SHA256}"
echo ""
echo -e "${BOLD}  Next steps:${NC}"
echo ""
echo -e "  ${CYAN}1. GitHub Release:${NC}"
echo -e "     • Create repo:  https://github.com/YOUR_USERNAME/archvault"
echo -e "     • Push code:    git push origin main"
echo -e "     • Create tag:   git tag -a ${TAG} -m 'Release ${TAG}'"
echo -e "     • Push tag:     git push origin ${TAG}"
echo -e "     • Upload:       ${TARBALL}"
echo ""
echo -e "  ${CYAN}2. AUR Upload:${NC}"
echo -e "     • Update YOUR_USERNAME in PKGBUILD and .SRCINFO"
echo -e "     • Update sha256sums after GitHub generates the tarball"
echo -e "     •   sha256sum of GitHub's tarball will differ from local!"
echo -e "     •   Download it, re-hash, update PKGBUILD + .SRCINFO"
echo -e "     • Clone AUR repo:  git clone ssh://aur@aur.archlinux.org/${PKGNAME}.git"
echo -e "     • Copy PKGBUILD + .SRCINFO into the AUR repo"
echo -e "     • Commit & push to AUR"
echo ""
echo -e "  ${CYAN}3. Test install:${NC}"
echo -e "     • yay -S ${PKGNAME}   (after AUR upload)"
echo -e "     • Or:  makepkg -si   (from AUR directory)"
echo ""
