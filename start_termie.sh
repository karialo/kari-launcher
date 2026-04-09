#!/usr/bin/env bash
set -Eeuo pipefail

LAUNCHER_SERVICE="${LAUNCHER_SERVICE:-kari-dashboard.service}"
TERMIE_SERVICE="${TERMIE_SERVICE:-termie.service}"

echo "Stopping launcher..."
sudo systemctl stop "${LAUNCHER_SERVICE}"

echo "Starting Termie..."
sudo systemctl start "${TERMIE_SERVICE}"

echo
systemctl --no-pager --full status "${TERMIE_SERVICE}" "${LAUNCHER_SERVICE}" | sed -n '1,40p'
