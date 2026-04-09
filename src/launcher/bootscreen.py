#!/usr/bin/env python3
from __future__ import annotations

import os
import time
from pathlib import Path

os.environ.setdefault("SDL_VIDEODRIVER", "dummy")

import pygame
from PIL import Image

from .dashboard import CONFIG_PATH, DisplayHATMini, WaveshareDisplay, ensure_config
from .waveshare_1in44 import Waveshare144Display

BOOTSCREEN_FILENAME = "booting.png"


def _asset_path(filename: str) -> Path | None:
    here = Path(__file__).resolve()
    candidates = [
        here.parents[2] / filename,
        here.with_name(filename),
    ]
    for candidate in candidates:
        if candidate.exists():
            return candidate
    return None


def _load_surface(size: tuple[int, int]) -> pygame.Surface | None:
    asset = _asset_path(BOOTSCREEN_FILENAME)
    if asset is None:
        return None
    try:
        raw = Image.open(asset).convert("RGB")
    except Exception:
        return None

    sw, sh = size
    iw, ih = raw.size
    if iw <= 0 or ih <= 0:
        return None

    scale = min(sw / float(iw), sh / float(ih))
    nw = max(1, int(iw * scale))
    nh = max(1, int(ih * scale))
    scaled = raw.resize((nw, nh), Image.Resampling.LANCZOS)

    canvas = Image.new("RGB", (sw, sh), (0, 0, 0))
    canvas.paste(scaled, ((sw - nw) // 2, (sh - nh) // 2))
    surface = pygame.image.fromstring(canvas.tobytes(), canvas.size, "RGB")
    return surface.copy()


def _build_display(config: dict) -> tuple[object | None, str, tuple[int, int]]:
    hw = config.get("hardware", {}) if isinstance(config.get("hardware"), dict) else {}
    requested_backend = str(hw.get("backend", "auto")).strip().lower()
    display = None
    backend = "preview"

    if requested_backend in ("auto", "waveshare", "waveshare_1in3", "st7789"):
        try:
            display = WaveshareDisplay(
                spi_port=int(hw.get("spi_port", 0)),
                spi_cs=int(hw.get("spi_cs", 0)),
                dc_pin=int(hw.get("dc_pin", 25)),
                rst_pin=int(hw.get("rst_pin", 27)),
                backlight_pin=int(hw.get("backlight_pin", 24)),
                rotation=int(hw.get("rotation", 90)),
                invert=bool(hw.get("invert", True)),
                spi_speed_hz=int(hw.get("spi_speed_hz", 24_000_000)),
            )
            backend = "waveshare"
        except Exception:
            display = None
    elif requested_backend in ("waveshare_1in44", "st7735", "waveshare_144"):
        try:
            display = Waveshare144Display(
                spi_port=int(hw.get("spi_port", 0)),
                spi_cs=int(hw.get("spi_cs", 0)),
                dc_pin=int(hw.get("dc_pin", 25)),
                rst_pin=int(hw.get("rst_pin", 27)),
                backlight_pin=int(hw.get("backlight_pin", 24)),
                rotation=int(hw.get("rotation", 0)),
                invert=bool(hw.get("invert", False)),
                spi_speed_hz=int(hw.get("spi_speed_hz", 9_000_000)),
            )
            backend = "waveshare"
        except Exception:
            display = None

    if display is None and DisplayHATMini is not None:
        try:
            use_backlight_pwm = bool(config.get("backlight_pwm", False))
            display = DisplayHATMini(None, backlight_pwm=use_backlight_pwm)
            display.init()
            bl = float(config.get("backlight_level", 1.0))
            if use_backlight_pwm:
                display.set_backlight(bl)
            else:
                display.set_backlight(1.0 if bl >= 0.5 else 0.0)
            backend = "displayhatmini"
        except Exception:
            display = None

    if display is None:
        return None, backend, (240, 240)

    width = int(getattr(display, "width", getattr(display, "WIDTH", 240)))
    height = int(getattr(display, "height", getattr(display, "HEIGHT", 240)))
    return display, backend, (width, height)


def _update_display(display: object, backend: str, surface: pygame.Surface) -> None:
    if backend == "waveshare":
        display.display_surface(surface)
        return
    display.st7789.set_window()
    pixelbytes = pygame.transform.rotate(surface, 180).convert(16, 0).get_buffer()
    swapped = bytearray(pixelbytes)
    swapped[0::2], swapped[1::2] = swapped[1::2], swapped[0::2]
    for i in range(0, len(swapped), 4096):
        display.st7789.data(swapped[i : i + 4096])


def _draw_fade(display: object, backend: str, splash: pygame.Surface, alpha: int) -> None:
    frame = pygame.Surface(splash.get_size())
    frame.fill((0, 0, 0))
    img = splash.copy()
    img.set_alpha(max(0, min(255, alpha)))
    frame.blit(img, (0, 0))
    _update_display(display, backend, frame)


def main() -> int:
    pygame.init()
    try:
        config = ensure_config(CONFIG_PATH)
        display, backend, size = _build_display(config)
        if display is None:
            return 0
        splash = _load_surface(size)
        if splash is None:
            return 0

        steps = 18
        for idx in range(steps + 1):
            alpha = int((255 * idx) / steps)
            _draw_fade(display, backend, splash, alpha)
            time.sleep(0.05)
        return 0
    finally:
        pygame.quit()


if __name__ == "__main__":
    raise SystemExit(main())
