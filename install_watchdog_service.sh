#!/usr/bin/env bash
set -Eeuo pipefail

SERVICE_NAME="kari-watchdog.service"
TIMER_NAME="kari-watchdog.timer"
SERVICE_DST="/etc/systemd/system/${SERVICE_NAME}"
TIMER_DST="/etc/systemd/system/${TIMER_NAME}"
ENV_DST="/etc/default/kari-watchdog"

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SERVICE_SRC="${HERE}/systemd/${SERVICE_NAME}"
TIMER_SRC="${HERE}/systemd/${TIMER_NAME}"
ENV_SRC="${HERE}/systemd/kari-watchdog.env"

if [[ "${EUID}" -ne 0 ]]; then
  echo "Run as root: sudo ./install_watchdog_service.sh"
  exit 1
fi

for f in "${SERVICE_SRC}" "${TIMER_SRC}" "${ENV_SRC}" "${HERE}/bin/watchdog"; do
  if [[ ! -f "${f}" ]]; then
    echo "Missing required file: ${f}"
    exit 1
  fi
done

TMP_UNIT="$(mktemp)"
sed -e "s#{{PROJECT_DIR}}#${HERE}#g" "${SERVICE_SRC}" > "${TMP_UNIT}"
install -m 0644 "${TMP_UNIT}" "${SERVICE_DST}"
rm -f "${TMP_UNIT}"

install -m 0644 "${TIMER_SRC}" "${TIMER_DST}"
if [[ ! -f "${ENV_DST}" ]]; then
  install -m 0644 "${ENV_SRC}" "${ENV_DST}"
  echo "Installed default config: ${ENV_DST}"
else
  echo "Keeping existing config: ${ENV_DST}"
  install -m 0644 "${ENV_SRC}" "${ENV_DST}.new"
  echo "Wrote updated defaults to: ${ENV_DST}.new"
  echo "Review and merge new keys/values into ${ENV_DST}"
fi

systemctl daemon-reload
systemctl enable --now "${TIMER_NAME}"
systemctl start "${SERVICE_NAME}" || true

echo
echo "Installed ${SERVICE_NAME} and enabled ${TIMER_NAME}"
systemctl --no-pager --full status "${SERVICE_NAME}" || true
systemctl --no-pager --full status "${TIMER_NAME}" || true
