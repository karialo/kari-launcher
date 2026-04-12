#!/usr/bin/env bash
# Description: Stops monitior mode on the laptops built in wireless card. 
VERSION="0.1.0"
set -Eeuo pipefail

sudo ip link set wlan1 down
sudo iw dev wlan1 set type managed
sleep 1
sudo ip link set wlan1 up

