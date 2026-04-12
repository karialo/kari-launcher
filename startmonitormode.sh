#!/usr/bin/env bash
# Description: Script to start monitor mode on laptop wifi card
VERSION="0.1.0"
set -Eeuo pipefail

sudo ip link set wlan1 down
sudo iw dev wlan1 set type monitor
sudo ip link set wlan1 up
iw dev wlan1 info
