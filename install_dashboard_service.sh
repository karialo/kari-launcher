#!/usr/bin/env bash
set -Eeuo pipefail

SERVICE_NAMES=("kari-bootscreen.service" "kari-dashboard.service" "termie.service")
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
RUN_USER="${KARI_RUN_USER:-${SUDO_USER:-${USER:-$(id -un)}}}"
if [[ "${RUN_USER}" == "root" ]]; then
  REPO_OWNER="$(stat -c '%U' "${HERE}" 2>/dev/null || true)"
  if [[ -n "${REPO_OWNER}" && "${REPO_OWNER}" != "root" ]]; then
    RUN_USER="${REPO_OWNER}"
  else
    echo "Could not infer the non-root launcher user; rerun with KARI_RUN_USER=<username>"
    exit 1
  fi
fi
RUN_HOME="$(getent passwd "${RUN_USER}" | cut -d: -f6 || true)"
if [[ -z "${RUN_HOME}" ]]; then
  RUN_HOME="/home/${RUN_USER}"
fi
CFG_PATH="${KARI_DASH_CONFIG:-${RUN_HOME}/.config/launcher/dashboard.json}"

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
systemctl disable termie.service >/dev/null 2>&1 || true

echo
echo "Installed kari-bootscreen.service, kari-dashboard.service, and termie.service"
echo "Started kari-bootscreen.service and kari-dashboard.service"
systemctl --no-pager --full status kari-bootscreen.service || true
echo
systemctl --no-pager --full status kari-dashboard.service || true
echo
systemctl --no-pager --full status termie.service || true
