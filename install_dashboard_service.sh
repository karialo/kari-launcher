#!/usr/bin/env bash
set -Eeuo pipefail

SERVICE_NAMES=("kari-bootscreen.service" "kari-dashboard.service")
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
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

for SERVICE_NAME in "${SERVICE_NAMES[@]}"; do
  SERVICE_SRC="${HERE}/systemd/${SERVICE_NAME}"
  SERVICE_DST="/etc/systemd/system/${SERVICE_NAME}"
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
done

systemctl daemon-reload
systemctl enable --now kari-bootscreen.service
systemctl enable --now kari-dashboard.service

echo
echo "Installed and started kari-bootscreen.service and kari-dashboard.service"
systemctl --no-pager --full status kari-bootscreen.service || true
echo
systemctl --no-pager --full status kari-dashboard.service || true
