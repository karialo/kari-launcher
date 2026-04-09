from __future__ import annotations

import time

try:
    import numpy
except Exception:
    numpy = None  # type: ignore[assignment]

import pygame
from PIL import Image
try:
    import RPi.GPIO as GPIO
except Exception:
    GPIO = None  # type: ignore[assignment]
try:
    import spidev
except Exception:
    spidev = None  # type: ignore[assignment]


class Waveshare144Display:
    """Waveshare 1.44in ST7735 display backend.

    The launcher UI is still authored around a 240x240 logical canvas, so this
    backend keeps a 240x240 logical surface and scales it down onto the
    physical 128x128 panel.
    """

    def __init__(
        self,
        spi_port: int,
        spi_cs: int,
        dc_pin: int,
        rst_pin: int,
        backlight_pin: int,
        rotation: int = 0,
        invert: bool = False,
        spi_speed_hz: int = 9_000_000,
    ):
        if spidev is None or GPIO is None or numpy is None:
            raise RuntimeError("waveshare 1.44 display dependencies not available")
        self.rotation = int(rotation)
        self.invert = bool(invert)
        self.width = 240
        self.height = 240
        self.panel_width = 128
        self.panel_height = 128
        self._x_adjust = 2
        self._y_adjust = 1

        GPIO.setmode(GPIO.BCM)
        GPIO.setwarnings(False)
        GPIO.setup(int(dc_pin), GPIO.OUT)
        GPIO.setup(int(rst_pin), GPIO.OUT)
        GPIO.setup(int(backlight_pin), GPIO.OUT)

        self._spi = spidev.SpiDev(int(spi_port), int(spi_cs))
        self._spi.mode = 0b00
        self._spi.max_speed_hz = int(spi_speed_hz)
        self._dc = int(dc_pin)
        self._rst = int(rst_pin)
        self._bl = int(backlight_pin)

        self._write_pin(self._bl, False)
        time.sleep(0.05)
        self._init_panel()
        self.set_backlight(1.0)

    def _write_pin(self, pin: int, value: bool) -> None:
        GPIO.output(pin, GPIO.HIGH if value else GPIO.LOW)

    def _command(self, value: int) -> None:
        self._write_pin(self._dc, False)
        self._spi.writebytes([value & 0xFF])

    def _data(self, *values: int) -> None:
        if not values:
            return
        self._write_pin(self._dc, True)
        self._spi.writebytes([value & 0xFF for value in values])

    def _reset(self) -> None:
        self._write_pin(self._rst, True)
        time.sleep(0.1)
        self._write_pin(self._rst, False)
        time.sleep(0.1)
        self._write_pin(self._rst, True)
        time.sleep(0.1)

    def _init_panel(self) -> None:
        self._reset()
        self._command(0xB1)
        self._data(0x01, 0x2C, 0x2D)
        self._command(0xB2)
        self._data(0x01, 0x2C, 0x2D)
        self._command(0xB3)
        self._data(0x01, 0x2C, 0x2D, 0x01, 0x2C, 0x2D)
        self._command(0xB4)
        self._data(0x07)
        self._command(0xC0)
        self._data(0xA2, 0x02, 0x84)
        self._command(0xC1)
        self._data(0xC5)
        self._command(0xC2)
        self._data(0x0A, 0x00)
        self._command(0xC3)
        self._data(0x8A, 0x2A)
        self._command(0xC4)
        self._data(0x8A, 0xEE)
        self._command(0xC5)
        self._data(0x0E)
        self._command(0xE0)
        self._data(0x0F, 0x1A, 0x0F, 0x18, 0x2F, 0x28, 0x20, 0x22, 0x1F, 0x1B, 0x23, 0x37, 0x00, 0x07, 0x02, 0x10)
        self._command(0xE1)
        self._data(0x0F, 0x1B, 0x0F, 0x17, 0x33, 0x2C, 0x29, 0x2E, 0x30, 0x30, 0x39, 0x3F, 0x00, 0x07, 0x03, 0x10)
        self._command(0xF0)
        self._data(0x01)
        self._command(0xF6)
        self._data(0x00)
        self._command(0x3A)
        self._data(0x05)
        self._command(0x36)
        self._data(0x68)
        self._command(0x11)
        time.sleep(0.12)
        if self.invert:
            self._command(0x21)
        self._command(0x29)
        time.sleep(0.02)

    def _set_window(self, x0: int, y0: int, x1: int, y1: int) -> None:
        self._command(0x2A)
        self._data(0x00, (x0 + self._x_adjust) & 0xFF, 0x00, ((x1 - 1) + self._x_adjust) & 0xFF)
        self._command(0x2B)
        self._data(0x00, (y0 + self._y_adjust) & 0xFF, 0x00, ((y1 - 1) + self._y_adjust) & 0xFF)
        self._command(0x2C)

    def set_backlight(self, value: float) -> None:
        self._write_pin(self._bl, float(value) > 0.0)

    def set_led(self, *_args: float) -> None:
        return

    def display_surface(self, surface: pygame.Surface) -> None:
        raw = pygame.image.tostring(surface, "RGB")
        image = Image.frombytes("RGB", surface.get_size(), raw)
        if self.rotation:
            image = image.rotate(-self.rotation, expand=False)
        image = image.resize((self.panel_width, self.panel_height), Image.Resampling.LANCZOS).convert("RGB")
        img = numpy.asarray(image)
        pix = numpy.zeros((self.panel_width, self.panel_height, 2), dtype=numpy.uint8)
        pix[..., [0]] = numpy.add(numpy.bitwise_and(img[..., [0]], 0xF8), numpy.right_shift(img[..., [1]], 5))
        pix[..., [1]] = numpy.add(
            numpy.bitwise_and(numpy.left_shift(img[..., [1]], 3), 0xE0),
            numpy.right_shift(img[..., [2]], 3),
        )
        buf = pix.flatten().tolist()
        self._set_window(0, 0, self.panel_width, self.panel_height)
        self._write_pin(self._dc, True)
        for i in range(0, len(buf), 4096):
            self._spi.writebytes(buf[i : i + 4096])
