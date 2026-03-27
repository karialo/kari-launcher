#!/usr/bin/env bash
set -Eeuo pipefail

SERVICE_NAME="kari-dashboard.service"
SERVICE_DST="/etc/systemd/system/${SERVICE_NAME}"
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SERVICE_SRC="${HERE}/systemd/${SERVICE_NAME}"
RUN_USER="${SUDO_USER:-kali}"
RUN_HOME="$(getent passwd "${RUN_USER}" | cut -d: -f6 || true)"
if [[ -z "${RUN_HOME}" ]]; then
  RUN_HOME="/home/${RUN_USER}"
fi
CFG_PATH="${RUN_HOME}/.config/launcher/dashboard.json"

if [[ "${EUID}" -ne 0 ]]; then
  echo "Run as root: sudo ./install_dashboard_service.sh"
  exit 1
fi

if [[ ! -f "${SERVICE_SRC}" ]]; then
  echo "Missing service template: ${SERVICE_SRC}"
  exit 1
fi

TMP_UNIT="$(mktemp)"
sed \
  -e "s#{{PROJECT_DIR}}#${HERE}#g" \
  -e "s#{{DASH_CONFIG}}#${CFG_PATH}#g" \
  "${SERVICE_SRC}" > "${TMP_UNIT}"

install -m 0644 "${TMP_UNIT}" "${SERVICE_DST}"
rm -f "${TMP_UNIT}"
systemctl daemon-reload
systemctl enable --now "${SERVICE_NAME}"

echo
echo "Installed and started ${SERVICE_NAME}"
systemctl --no-pager --full status "${SERVICE_NAME}" || true
