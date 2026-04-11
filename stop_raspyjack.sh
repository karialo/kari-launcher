#!/usr/bin/env bash
set -Eeuo pipefail

# Example launcher-managed RaspyJack stop wrapper.
# Edit the variables below for your own install.

LAUNCHER_SERVICE="${LAUNCHER_SERVICE:-kari-dashboard.service}"
if [[ -z "${RASPYJACK_ROOT:-}" ]]; then
  if [[ -d /root/Raspyjack ]]; then
    RASPYJACK_ROOT="/root/Raspyjack"
  else
    RASPYJACK_ROOT="${HOME}/Projects/Raspyjack"
  fi
fi
RJ_SERVICES=(
  "${RJ_CORE_SERVICE:-raspyjack.service}"
  "${RJ_DEVICE_SERVICE:-raspyjack-device.service}"
  "${RJ_WEB_SERVICE:-raspyjack-webui.service}"
)
RJ_EXTRA_SERVICES="${RJ_EXTRA_SERVICES-raspyjack-caddy-autoconfig.service raspyjack-pin-wifi.service}"
if [[ -n "${RJ_EXTRA_SERVICES}" ]]; then
  read -r -a RJ_EXTRA_SERVICE_ARRAY <<< "${RJ_EXTRA_SERVICES}"
  RJ_SERVICES+=("${RJ_EXTRA_SERVICE_ARRAY[@]}")
fi

show_panel_message() {
  local title="$1"
  local detail="${2:-}"
  RJ_MSG_TITLE="${title}" RJ_MSG_DETAIL="${detail}" RASPYJACK_ROOT="${RASPYJACK_ROOT}" python3 - <<'PYMSG' || true
import os
import sys
from PIL import Image, ImageDraw, ImageFont

root = os.environ["RASPYJACK_ROOT"]
sys.path.insert(0, root)
import LCD_1in44  # type: ignore

title = os.environ.get("RJ_MSG_TITLE", "Please wait")
detail = os.environ.get("RJ_MSG_DETAIL", "")
rj_lcd = os.environ.get("RJ_LCD", "").strip().lower()
panel_w = int(os.environ.get("RJ_PANEL_WIDTH", "0") or 0)
panel_h = int(os.environ.get("RJ_PANEL_HEIGHT", "0") or 0)
if panel_w <= 0 or panel_h <= 0:
    if rj_lcd == "st7789":
        panel_w = 240
        panel_h = 240
    else:
        panel_w = int(getattr(LCD_1in44, "LCD_WIDTH", 128))
        panel_h = int(getattr(LCD_1in44, "LCD_HEIGHT", 128))

lcd = LCD_1in44.LCD()
lcd.LCD_Init(LCD_1in44.SCAN_DIR_DFT)
panel_w = int(lcd.width)
panel_h = int(lcd.height)

img = Image.new("RGB", (panel_w, panel_h), (4, 10, 12))
draw = ImageDraw.Draw(img)
try:
    title_font = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", max(14, panel_w // 12))
    body_font = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", max(10, panel_w // 18))
except Exception:
    title_font = ImageFont.load_default()
    body_font = ImageFont.load_default()

def centered(text, y, font, fill):
    box = draw.textbbox((0, 0), text, font=font)
    x = max(0, (panel_w - (box[2] - box[0])) // 2)
    draw.text((x, y), text, font=font, fill=fill)

draw.rectangle((10, 10, panel_w - 11, panel_h - 11), outline=(37, 210, 143), width=2)
centered(title[:28], panel_h // 2 - 34, title_font, (145, 255, 203))
if detail:
    centered(detail[:34], panel_h // 2 + 2, body_font, (220, 238, 230))
centered("please wait", panel_h // 2 + 30, body_font, (94, 146, 130))
try:
    lcd.LCD_ShowImage(img, 0, 0)
except Exception:
    pass
PYMSG
}

show_panel_message "Returning" "to launcher"

RJ_AVAILABLE_SERVICES=()
for svc in "${RJ_SERVICES[@]}"; do
  if systemctl list-unit-files "$svc" --no-legend 2>/dev/null | awk '{print $1}' | grep -Fxq "$svc"; then
    RJ_AVAILABLE_SERVICES+=("$svc")
  else
    echo "Skipping missing RaspyJack unit: $svc"
  fi
done

echo "Stopping RaspyJack stack..."
if [[ "${#RJ_AVAILABLE_SERVICES[@]}" -gt 0 ]]; then
  sudo systemctl stop "${RJ_AVAILABLE_SERVICES[@]}"
else
  echo "No RaspyJack systemd units were found; continuing with process/display cleanup."
fi

# Wait until service-managed RaspyJack is actually gone.
for _ in $(seq 1 40); do
  active=0
  for svc in "${RJ_AVAILABLE_SERVICES[@]}"; do
    if systemctl is-active --quiet "$svc"; then
      active=1
      break
    fi
  done
  if pgrep -f "${RASPYJACK_ROOT}/(raspyjack|device_server|web_server)\\.py" >/dev/null 2>&1; then
    active=1
  fi
  if [[ "$active" -eq 0 ]]; then
    break
  fi
  sleep 0.25
done

sleep 0.5

echo "Resetting LCD..."
if ! python3 - <<'PY2'
import RPi.GPIO as GPIO
import time

RST = 27
BL = 24

GPIO.setmode(GPIO.BCM)
GPIO.setwarnings(False)
GPIO.setup(RST, GPIO.OUT)
GPIO.setup(BL, GPIO.OUT)
GPIO.output(BL, GPIO.LOW)
GPIO.output(RST, GPIO.HIGH)
time.sleep(0.05)
GPIO.output(RST, GPIO.LOW)
time.sleep(0.08)
GPIO.output(RST, GPIO.HIGH)
time.sleep(0.12)
GPIO.output(BL, GPIO.HIGH)
time.sleep(0.15)
GPIO.cleanup()
PY2
then
  echo "LCD reset failed; continuing launcher return." >&2
fi

echo "Blanking panel..."
if ! RASPYJACK_ROOT="${RASPYJACK_ROOT}" python3 - <<'PY3'
import os
import sys
from PIL import Image

root = os.environ["RASPYJACK_ROOT"]
sys.path.insert(0, root)
import LCD_1in44  # type: ignore

rj_lcd = os.environ.get("RJ_LCD", "").strip().lower()
panel_w = int(os.environ.get("RJ_PANEL_WIDTH", "0") or 0)
panel_h = int(os.environ.get("RJ_PANEL_HEIGHT", "0") or 0)
if panel_w <= 0 or panel_h <= 0:
    if rj_lcd == "st7789":
        panel_w = 240
        panel_h = 240
    else:
        panel_w = int(getattr(LCD_1in44, "LCD_WIDTH", 128))
        panel_h = int(getattr(LCD_1in44, "LCD_HEIGHT", 128))

lcd = LCD_1in44.LCD()
lcd.LCD_Init(LCD_1in44.SCAN_DIR_DFT)
panel_w = int(lcd.width)
panel_h = int(lcd.height)
img = Image.new("RGB", (panel_w, panel_h), (0, 0, 0))
try:
    lcd.LCD_ShowImage(img, 0, 0)
except Exception as exc:
    print(f"Panel blanking failed: {exc}", file=sys.stderr)
    raise SystemExit(1)
PY3
then
  echo "Panel blanking failed; continuing launcher return." >&2
fi

echo "Starting launcher..."
show_panel_message "Starting" "launcher"
sudo systemctl restart "${LAUNCHER_SERVICE}"

echo
echo "Service status:"
if [[ "${#RJ_AVAILABLE_SERVICES[@]}" -gt 0 ]]; then
  systemctl --no-pager --full status "${LAUNCHER_SERVICE}" "${RJ_AVAILABLE_SERVICES[@]}" | sed -n '1,60p' || true
else
  systemctl --no-pager --full status "${LAUNCHER_SERVICE}" | sed -n '1,60p' || true
fi

echo
echo "If your RaspyJack install is not service-based, replace the systemctl calls in this wrapper with your own shutdown logic."
