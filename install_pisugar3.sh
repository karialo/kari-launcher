#!/usr/bin/env bash
set -Eeuo pipefail

# Installs PiSugar Power Manager + runtime deps used by the launcher.
# Prefers GitHub releases, then falls back to the PiSugar CDN installer.
# Usage:
#   sudo ./install_pisugar3.sh
#   sudo ./install_pisugar3.sh 2.0.0

if [[ "${EUID}" -ne 0 ]]; then
  echo "Run as root: sudo ./install_pisugar3.sh [version]"
  exit 1
fi

ARCH="$(dpkg --print-architecture)"
case "${ARCH}" in
  arm64|armhf) ;;
  *)
    echo "Unsupported architecture for PiSugar package: ${ARCH}"
    exit 1
    ;;
esac

REQ_PKGS=(
  iw
  netcat-openbsd
  avahi-daemon
  ca-certificates
  curl
  wget
)

echo "[1/6] Installing dependencies..."
apt update
apt install -y "${REQ_PKGS[@]}"

if [[ -n "${1:-}" ]]; then
  VER="${1#v}"
else
  echo "[2/6] Resolving latest PiSugar release..."
  VER="$(curl -fsSL "https://api.github.com/repos/PiSugar/PiSugarPowerManager/releases/latest" \
    | sed -n 's/.*"tag_name":[[:space:]]*"v\([^"]*\)".*/\1/p' \
    | head -n1 || true)"
  if [[ -z "${VER}" ]]; then
    VER="2.0.0"
    echo "Could not resolve latest release; falling back to v${VER}"
  fi
fi

echo "[3/6] Using PiSugar version target v${VER}"

installed=0
RELEASE_JSON="$(curl -fsSL "https://api.github.com/repos/PiSugar/PiSugarPowerManager/releases/tags/v${VER}" || true)"
DEB_URL="$(printf '%s' "${RELEASE_JSON}" \
  | sed -n 's/.*"browser_download_url":[[:space:]]*"\([^"]*pisugar-power-manager[^"]*'"${ARCH}"'\.deb\)".*/\1/p' \
  | head -n1)"

if [[ -n "${DEB_URL}" ]]; then
  DEB_FILE="/tmp/$(basename "${DEB_URL}")"
  echo "[4/6] Downloading ${DEB_URL}"
  if wget -O "${DEB_FILE}" "${DEB_URL}"; then
    echo "[5/6] Installing PiSugar package from GitHub..."
    apt install -y "${DEB_FILE}"
    installed=1
  fi
fi

if [[ "${installed}" -eq 0 ]]; then
  echo "[4/6] GitHub release install not available; falling back to PiSugar CDN installer..."
  TMP_SH="/tmp/pisugar-power-manager.sh"
  wget -O "${TMP_SH}" "https://cdn.pisugar.com/release/pisugar-power-manager.sh"
  echo "[5/6] Running CDN installer script..."
  bash "${TMP_SH}" -c release
fi

echo "[6/6] Enabling pisugar-server..."
systemctl enable --now pisugar-server
systemctl --no-pager --full status pisugar-server || true

echo
echo "PiSugar install complete."
echo "Next:"
echo "  1) Restart dashboard service: sudo systemctl restart kari-dashboard.service"
echo "  2) Verify battery API:"
echo "     echo \"get battery\" | nc -q 0 127.0.0.1 8423"
