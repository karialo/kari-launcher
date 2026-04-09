#!/usr/bin/env bash
set -Eeuo pipefail

LAUNCHER_SERVICE="${LAUNCHER_SERVICE:-kari-dashboard.service}"
TERMIE_SERVICE="${TERMIE_SERVICE:-termie.service}"
RETURN_UNIT="${RETURN_UNIT:-termie-return-launcher}"

echo "Scheduling launcher return..."
if command -v systemd-run >/dev/null 2>&1; then
  sudo systemd-run --unit "${RETURN_UNIT}" --collect /usr/bin/env bash -lc \
    "sleep 0.6; systemctl restart '${LAUNCHER_SERVICE}'" >/dev/null
else
  sudo /usr/bin/env bash -lc "nohup bash -lc 'sleep 0.6; systemctl restart \"${LAUNCHER_SERVICE}\"' >/dev/null 2>&1 &"
fi

echo "Stopping Termie..."
sudo systemctl stop "${TERMIE_SERVICE}"

echo
systemctl --no-pager --full status "${TERMIE_SERVICE}" "${LAUNCHER_SERVICE}" "${RETURN_UNIT}.service" | sed -n '1,60p'
