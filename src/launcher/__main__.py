#!/usr/bin/env python3
"""
KARI Launcher (ST7789 + gpiod buttons, relaunch-safe)

Fixes:
- Kills stale launcher instance holding SPI/lock
- Uses /dev/gpiochip via gpiod (no /dev/mem, no root needed)
- Compatible with libgpiod v1 (get_line) AND v2 (request_lines)
"""

from __future__ import annotations

import atexit
import fcntl
import json
import os
import signal
import sys
import time
from pathlib import Path
from typing import Optional

from PIL import Image, ImageDraw, ImageFont

try:
    import ST7789 as st7789  # Pimoroni st7789
except Exception as e:
    raise RuntimeError("Missing ST7789 python module. Install the DisplayHAT Mini/ST7789 deps.") from e


# ──────────────────────────────────────────────────────────────────────────────
# Config
# ──────────────────────────────────────────────────────────────────────────────

CONFIG_DIR = Path.home() / ".config" / "launcher"
APPS_PATH = CONFIG_DIR / "apps.json"

LOCK_PATH = "/tmp/kari-launcher.lock"
PID_PATH = "/tmp/kari-launcher.pid"

# DisplayHAT Mini (ST7789) defaults
DISPLAY_WIDTH = 240
DISPLAY_HEIGHT = 240
DISPLAY_ROTATION = 180  # 0/90/180/270 depending on your mount/case

# Buttons (BCM)
BTN_UP_PIN = 5
BTN_DOWN_PIN = 6
BTN_SELECT_PIN = 16
BTN_BACK_PIN = 20

# Active-low buttons are typical: pressed -> 0
BTN_ACTIVE_LOW = True

# UI
FONT_PATHS = [
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    "/usr/share/fonts/truetype/freefont/FreeSans.ttf",
]
FONT_SIZE = 18
TITLE_SIZE = 20

POLL_HZ = 30
DEBOUNCE_MS = 90


# ──────────────────────────────────────────────────────────────────────────────
# Utilities
# ──────────────────────────────────────────────────────────────────────────────

def _log(msg: str) -> None:
    print(f"[launcher] {msg}", flush=True)


def _pick_font(size: int) -> ImageFont.FreeTypeFont:
    for p in FONT_PATHS:
        if os.path.exists(p):
            return ImageFont.truetype(p, size=size)
    return ImageFont.load_default()


def _pick_gpiochip() -> str:
    # Prefer gpiochip0 (Raspberry Pi) but fall back if needed.
    candidates = ["/dev/gpiochip0", "/dev/gpiochip1", "/dev/gpiochip2"]
    for c in candidates:
        if os.path.exists(c):
            return c
    raise RuntimeError("No /dev/gpiochip* devices found. Is gpiochip enabled / kernel exposing GPIO?")


def acquire_lock() -> int:
    fd = os.open(LOCK_PATH, os.O_CREAT | os.O_RDWR, 0o600)
    fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    os.ftruncate(fd, 0)
    os.write(fd, str(os.getpid()).encode("utf-8"))
    os.fsync(fd)
    return fd


def kill_stale_instance() -> None:
    # Kill old instance if PID file exists and process is alive.
    if not os.path.exists(PID_PATH):
        return
    try:
        with open(PID_PATH, "r", encoding="utf-8") as f:
            pid_s = f.read().strip()
        if not pid_s:
            return
        pid = int(pid_s)
    except Exception:
        return

    # Is it alive?
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return
    except PermissionError:
        # If we can't signal it, don't hard fail.
        _log(f"stale PID {pid} exists but cannot signal (permission denied)")
        return

    _log(f"killing stale instance PID {pid}")
    try:
        os.kill(pid, signal.SIGTERM)
    except Exception:
        return

    # Give it a moment, then SIGKILL if needed
    for _ in range(15):
        time.sleep(0.1)
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return
        except Exception:
            break

    try:
        os.kill(pid, signal.SIGKILL)
    except Exception:
        pass


def write_pid() -> None:
    try:
        with open(PID_PATH, "w", encoding="utf-8") as f:
            f.write(str(os.getpid()))
    except Exception:
        pass


# ──────────────────────────────────────────────────────────────────────────────
# Apps
# ──────────────────────────────────────────────────────────────────────────────

def default_apps() -> list[dict]:
    return [
        {"name": "Shell", "cmd": "bash"},
        {"name": "Reboot", "cmd": "sudo reboot"},
        {"name": "Shutdown", "cmd": "sudo poweroff"},
    ]


def load_apps() -> list[dict]:
    if not APPS_PATH.exists():
        APPS_PATH.parent.mkdir(parents=True, exist_ok=True)
        with open(APPS_PATH, "w", encoding="utf-8") as f:
            json.dump(default_apps(), f, indent=2)
        return default_apps()

    try:
        with open(APPS_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, list):
            return [x for x in data if isinstance(x, dict) and "name" in x and "cmd" in x]
    except Exception as e:
        _log(f"failed to load apps.json: {e}")

    return default_apps()


# ──────────────────────────────────────────────────────────────────────────────
# Display
# ──────────────────────────────────────────────────────────────────────────────

def build_display() -> st7789.ST7789:
    disp = st7789.ST7789(
        height=DISPLAY_HEIGHT,
        width=DISPLAY_WIDTH,
        rotation=DISPLAY_ROTATION,
        port=0,
        cs=st7789.BG_SPI_CS_FRONT,  # Display HAT Mini default
        dc=9,
        backlight=13,
        spi_speed_hz=80 * 1000 * 1000,
    )
    disp.begin()
    return disp


def release_display(disp: Optional[st7789.ST7789]) -> None:
    if disp is None:
        return
    try:
        disp.set_backlight(0)
    except Exception:
        pass


def render_menu(apps: list[dict], selected: int, status: str) -> Image.Image:
    img = Image.new("RGB", (DISPLAY_WIDTH, DISPLAY_HEIGHT))
    draw = ImageDraw.Draw(img)

    font = _pick_font(FONT_SIZE)
    title_font = _pick_font(TITLE_SIZE)

    # Title
    draw.text((10, 8), "K.A.R.I Launcher", font=title_font)

    # Items
    y = 44
    for i, app in enumerate(apps):
        name = str(app.get("name", ""))
        prefix = "▶ " if i == selected else "  "
        draw.text((10, y), prefix + name, font=font)
        y += FONT_SIZE + 6
        if y > DISPLAY_HEIGHT - 30:
            break

    # Status line
    draw.text((10, DISPLAY_HEIGHT - 22), status, font=font)

    return img


# ──────────────────────────────────────────────────────────────────────────────
# Buttons (libgpiod v1+v2 compatible)
# ──────────────────────────────────────────────────────────────────────────────

class GPIODButtons:
    def __init__(self, pins: dict[str, int], active_low: bool = True):
        self.pins = pins
        self.active_low = active_low
        self._req = None  # v2 request handle
        self._lines = {}  # v1 line handles
        self._chip = None

        try:
            import gpiod  # python package: gpiod
        except Exception as e:
            raise RuntimeError(
                "Missing python 'gpiod' module in the launcher venv."
            ) from e

        self.gpiod = gpiod

        chip_path = _pick_gpiochip()
        self._chip = self.gpiod.Chip(chip_path)

        # libgpiod v1 uses chip.get_line(); v2 uses request_lines()/LineSettings.
        if hasattr(self._chip, "get_line"):
            # v1 API
            for name, bcm in self.pins.items():
                line = self._chip.get_line(bcm)
                line.request(consumer="kari-launcher", type=self.gpiod.LINE_REQ_DIR_IN)
                self._lines[name] = line
        else:
            # v2 API
            # Configure bias to make buttons stable without external resistors:
            # - active_low buttons typically idle HIGH, pressed LOW => use pull-up
            # - active_high buttons idle LOW, pressed HIGH => use pull-down
            try:
                LineSettings = self.gpiod.LineSettings
                Direction = self.gpiod.line.Direction
                Bias = self.gpiod.line.Bias
            except Exception as e:
                raise RuntimeError(
                    "gpiod module looks like v2 but missing expected symbols (LineSettings/Direction/Bias)."
                ) from e

            bias = Bias.PULL_UP if self.active_low else Bias.PULL_DOWN
            settings = LineSettings(direction=Direction.INPUT, bias=bias)
            config = {bcm: settings for bcm in self.pins.values()}

            # Prefer module-level request_lines if present; otherwise try chip.request_lines
            if hasattr(self.gpiod, "request_lines"):
                self._req = self.gpiod.request_lines(
                    chip_path,
                    consumer="kari-launcher",
                    config=config,
                )
            elif hasattr(self._chip, "request_lines"):
                self._req = self._chip.request_lines(
                    consumer="kari-launcher",
                    config=config,
                )
            else:
                raise RuntimeError("Unsupported gpiod API: no get_line() and no request_lines().")

    def close(self) -> None:
        if self._req is not None:
            try:
                if hasattr(self._req, "release"):
                    self._req.release()
                elif hasattr(self._req, "close"):
                    self._req.close()
            except Exception:
                pass
            self._req = None
        else:
            for line in self._lines.values():
                try:
                    line.release()
                except Exception:
                    pass
            self._lines.clear()

        try:
            if self._chip:
                self._chip.close()
        except Exception:
            pass

    def pressed(self, name: str) -> bool:
        if self._req is not None:
            bcm = self.pins[name]
            vraw = self._req.get_value(bcm)
            v = int(getattr(vraw, "value", vraw))  # enum -> int
        else:
            v = int(self._lines[name].get_value())

        return (v == 0) if self.active_low else (v == 1)


class DebouncedButtons:
    def __init__(self, buttons: GPIODButtons, debounce_ms: int = 80):
        self.buttons = buttons
        self.debounce_ms = debounce_ms
        self._last = {k: 0.0 for k in buttons.pins.keys()}
        self._state = {k: False for k in buttons.pins.keys()}

    def poll(self) -> dict[str, bool]:
        # Returns dict of "edge" events: True when a button is newly pressed
        now = time.time()
        events: dict[str, bool] = {}
        for k in self._state.keys():
            pressed_now = self.buttons.pressed(k)
            was = self._state[k]
            if pressed_now != was:
                # changed state
                if (now - self._last[k]) * 1000.0 >= self.debounce_ms:
                    self._last[k] = now
                    self._state[k] = pressed_now
                    if pressed_now:
                        events[k] = True
        return events


# ──────────────────────────────────────────────────────────────────────────────
# Runner
# ──────────────────────────────────────────────────────────────────────────────

def launch_cmd(cmd: str) -> None:
    # fire and forget
    try:
        os.system(cmd)
    except Exception as e:
        _log(f"launch failed: {e}")


def main() -> int:
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)

    # Kill old instance before lock attempt
    kill_stale_instance()

    try:
        lock_fd = acquire_lock()
    except BlockingIOError:
        _log(f"already running (lock: {LOCK_PATH})")
        return 2

    write_pid()

    apps = load_apps()
    selected = 0
    status = f"{len(apps)} apps loaded"

    disp: Optional[st7789.ST7789] = None

    buttons = GPIODButtons(
        {"up": BTN_UP_PIN, "down": BTN_DOWN_PIN, "select": BTN_SELECT_PIN, "back": BTN_BACK_PIN},
        active_low=BTN_ACTIVE_LOW,
    )
    dbuttons = DebouncedButtons(buttons, debounce_ms=DEBOUNCE_MS)

    def cleanup() -> None:
        try:
            buttons.close()
        except Exception:
            pass
        try:
            release_display(disp)
        except Exception:
            pass
        try:
            os.close(lock_fd)
        except Exception:
            pass
        try:
            if os.path.exists(LOCK_PATH):
                os.remove(LOCK_PATH)
        except Exception:
            pass
        try:
            if os.path.exists(PID_PATH):
                os.remove(PID_PATH)
        except Exception:
            pass

    atexit.register(cleanup)

    def ensure_display() -> st7789.ST7789:
        nonlocal disp
        if disp is None:
            disp = build_display()
        return disp

    def redraw() -> None:
        d = ensure_display()
        img = render_menu(apps, selected, status)
        d.display(img)

    redraw()

    delay = 1.0 / float(POLL_HZ)

    try:
        while True:
            ev = dbuttons.poll()
            if ev:
                if ev.get("up"):
                    selected = (selected - 1) % len(apps)
                    status = apps[selected]["name"]
                    redraw()
                elif ev.get("down"):
                    selected = (selected + 1) % len(apps)
                    status = apps[selected]["name"]
                    redraw()
                elif ev.get("select"):
                    app = apps[selected]
                    status = f"launch: {app['name']}"
                    redraw()
                    launch_cmd(str(app["cmd"]))
                    status = f"done: {app['name']}"
                    redraw()
                elif ev.get("back"):
                    status = "bye"
                    redraw()
                    return 0

            time.sleep(delay)

    except KeyboardInterrupt:
        return 0
    finally:
        cleanup()


if __name__ == "__main__":
    raise SystemExit(main())
