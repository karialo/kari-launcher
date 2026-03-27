#!/usr/bin/env python3

import os
import time

from PIL import Image, ImageOps
import RPi.GPIO as GPIO
import spidev

try:
    import ST7789 as _st7789_lib
except ImportError:
    try:
        import st7789 as _st7789_lib
    except ImportError:
        _st7789_lib = None


SCAN_DIR_DFT = None
_MAX_XFER = 4096


def _parse_rotation(raw_value):
    try:
        value = int(str(raw_value).strip())
    except Exception:
        value = 0
    if value not in (0, 90, 180, 270):
        print(f"⚠️  Invalid RJ_ROTATE={raw_value!r}; using 0")
        return 0
    return value


class LCD:
    def __init__(self):
        self.width = 240
        self.height = 240

        self._port = int(os.environ.get("RJ_SPI_PORT", "0"))
        self._cs = int(os.environ.get("RJ_SPI_CS", "0"))
        self._dc = int(os.environ.get("RJ_LCD_DC", "25"))
        self._rst = int(os.environ.get("RJ_LCD_RST", "27"))
        self._bl = int(os.environ.get("RJ_LCD_BL", "24"))
        self._spi_speed = int(os.environ.get("RJ_SPI_SPEED", "24000000"))
        self._rotate = _parse_rotation(os.environ.get("RJ_ROTATE", "0"))
        self._invert = os.environ.get("RJ_ST7789_INVERT", "1") != "0"
        self._bl_active_low = os.environ.get("RJ_LCD_BL_ACTIVE_LOW", "0") == "1"
        self._raw_madctl = int(os.environ.get("RJ_ST7789_MADCTL", "0x00"), 0) & 0xFF

        # Backend selection:
        # - auto: st7789 lib first, raw fallback
        # - lib: force st7789 lib
        # - raw: force hud.py-compatible raw SPI
        self._backend_mode = os.environ.get("RJ_ST7789_BACKEND", "raw").strip().lower()
        self._debug = os.environ.get("RJ_LCD_DEBUG", "0") == "1"

        self._max_fps = float(os.environ.get("RJ_LCD_MAX_FPS", "0") or 0.0)
        self._min_interval = (1.0 / self._max_fps) if self._max_fps > 0 else 0.0
        self._last_push = 0.0

        self._disp = None
        self._spi = None
        self._backend = None
        self._push_failed = False
        self._blank = Image.new("RGB", (self.width, self.height), "BLACK")

    # -------------------------
    # Generic helpers
    # -------------------------
    def _prepare_frame(self, pil_image, x=0, y=0):
        frame = pil_image
        if frame.mode != "RGB":
            frame = frame.convert("RGB")
        if self._rotate:
            # Keep rotation direction aligned with existing RaspyJack usage.
            frame = frame.rotate(-self._rotate, expand=True)
        if frame.size != (self.width, self.height):
            frame = ImageOps.fit(
                frame,
                (self.width, self.height),
                method=Image.NEAREST,
                centering=(0.5, 0.5),
            )
        if x or y:
            canvas = self._blank.copy()
            canvas.paste(frame, (int(x), int(y)))
            frame = canvas
        return frame

    # -------------------------
    # st7789 library backend
    # -------------------------
    def _create_lib_display(self):
        if _st7789_lib is None:
            raise ImportError("ST7789 library module not available")

        attempts = [
            {
                "port": self._port,
                "cs": self._cs,
                "dc": self._dc,
                "rst": self._rst,
                "backlight": self._bl,
                "width": self.width,
                "height": self.height,
                "rotation": 0,
                "spi_speed_hz": self._spi_speed,
                "offset_left": 0,
                "offset_top": 0,
                "invert": self._invert,
            },
            {
                "port": self._port,
                "cs": self._cs,
                "dc": self._dc,
                "rst": self._rst,
                "backlight": self._bl,
                "width": self.width,
                "height": self.height,
                "rotation": 0,
                "spi_speed_hz": self._spi_speed,
            },
            {
                "port": self._port,
                "cs": self._cs,
                "dc": self._dc,
                "rst": self._rst,
            },
        ]

        last_err = None
        for kwargs in attempts:
            try:
                disp = _st7789_lib.ST7789(**kwargs)
                if self._debug:
                    print(f"[LCD_ST7789] lib ctor kwargs: {kwargs}")
                return disp
            except TypeError as exc:
                last_err = exc
        raise last_err if last_err is not None else RuntimeError("ST7789 ctor failed")

    def _init_lib_backend(self):
        self._disp = self._create_lib_display()
        for meth in ("begin", "init", "initialize"):
            fn = getattr(self._disp, meth, None)
            if callable(fn):
                try:
                    fn()
                    if self._debug:
                        print(f"[LCD_ST7789] called {meth}()")
                except Exception as exc:
                    if self._debug:
                        print(f"[LCD_ST7789] {meth}() failed: {exc!r}")
                break
        try:
            if hasattr(self._disp, "set_backlight"):
                self._disp.set_backlight(1)
        except Exception:
            pass
        self._backend = "lib"

    def _present_lib(self, frame):
        if hasattr(self._disp, "display"):
            self._disp.display(frame)
            return
        if hasattr(self._disp, "image"):
            self._disp.image(frame)
            return
        if hasattr(self._disp, "show"):
            self._disp.show(frame)
            return
        raise AttributeError("display/image/show not found on ST7789 object")

    # -------------------------
    # Raw SPI backend (hud.py-compatible)
    # -------------------------
    def _spi_write(self, data):
        self._spi.xfer2(data)

    def _raw_cmd(self, c):
        GPIO.output(self._dc, GPIO.LOW)
        self._spi_write([c])

    def _raw_data(self, buf):
        GPIO.output(self._dc, GPIO.HIGH)
        if isinstance(buf, (bytes, bytearray, memoryview)):
            mv = memoryview(buf)
            for i in range(0, len(mv), _MAX_XFER):
                self._spi.xfer2(mv[i : i + _MAX_XFER].tolist())
        else:
            for i in range(0, len(buf), _MAX_XFER):
                self._spi.xfer2(buf[i : i + _MAX_XFER])

    def _raw_set_window(self, x0, y0, x1, y1):
        self._raw_cmd(0x2A)
        self._raw_data([0x00, x0, 0x00, x1])
        self._raw_cmd(0x2B)
        self._raw_data([0x00, y0, 0x00, y1])
        self._raw_cmd(0x2C)

    def _raw_hw_reset(self):
        GPIO.output(self._rst, GPIO.HIGH)
        time.sleep(0.05)
        GPIO.output(self._rst, GPIO.LOW)
        time.sleep(0.05)
        GPIO.output(self._rst, GPIO.HIGH)
        time.sleep(0.15)

    def _init_raw_backend(self):
        GPIO.setwarnings(False)
        GPIO.setmode(GPIO.BCM)
        GPIO.setup(self._dc, GPIO.OUT, initial=0)
        GPIO.setup(self._rst, GPIO.OUT, initial=1)
        bl_on = GPIO.LOW if self._bl_active_low else GPIO.HIGH
        bl_off = GPIO.HIGH if self._bl_active_low else GPIO.LOW
        GPIO.setup(self._bl, GPIO.OUT, initial=bl_off)

        self._spi = spidev.SpiDev()
        self._spi.open(self._port, self._cs)
        self._spi.max_speed_hz = self._spi_speed
        self._spi.mode = 0

        # Turn backlight on before first frame push.
        GPIO.output(self._bl, bl_on)

        self._raw_hw_reset()
        self._raw_cmd(0x01)  # SWRESET
        time.sleep(0.15)
        self._raw_cmd(0x11)  # SLPOUT
        time.sleep(0.15)
        self._raw_cmd(0x3A)  # COLMOD
        self._raw_data([0x55])  # RGB565
        self._raw_cmd(0x36)  # MADCTL
        self._raw_data([self._raw_madctl])
        self._raw_cmd(0x21 if self._invert else 0x20)  # INVON / INVOFF
        self._raw_cmd(0x29)  # DISPON
        time.sleep(0.10)

        self._backend = "raw"
        if self._debug:
            print(
                f"[LCD_ST7789] raw backend active port={self._port} cs={self._cs} "
                f"dc={self._dc} rst={self._rst} bl={self._bl} spi={self._spi_speed} "
                f"bl_active_low={self._bl_active_low} madctl=0x{self._raw_madctl:02X} invert={self._invert}"
            )

    def _image_to_rgb565(self, frame):
        # Match the proven hud.py orientation path.
        frame = frame.transpose(Image.ROTATE_270).convert("RGB")
        pix = frame.load()
        buf = bytearray(self.width * self.height * 2)
        i = 0
        for y in range(self.height):
            for x in range(self.width):
                r, g, b = pix[x, y]
                rgb565 = ((r & 0xF8) << 8) | ((g & 0xFC) << 3) | (b >> 3)
                buf[i] = (rgb565 >> 8) & 0xFF
                buf[i + 1] = rgb565 & 0xFF
                i += 2
        return bytes(buf)

    def _present_raw(self, frame):
        buf = self._image_to_rgb565(frame)
        self._raw_set_window(0, 0, self.width - 1, self.height - 1)
        self._raw_data(buf)

    # -------------------------
    # Public API
    # -------------------------
    def LCD_Init(self, scan_dir=None):
        if self._backend is not None:
            return 0

        forced_raw = self._backend_mode == "raw"
        forced_lib = self._backend_mode == "lib"

        if not forced_raw:
            try:
                self._init_lib_backend()
                if self._debug:
                    print("[LCD_ST7789] using library backend")
                return 0
            except Exception as exc:
                if forced_lib:
                    raise
                if self._debug:
                    print(f"[LCD_ST7789] library backend failed: {exc!r}")

        self._init_raw_backend()
        if self._debug:
            print("[LCD_ST7789] using raw backend")
        return 0

    def LCD_ShowImage(self, pil_image, x=0, y=0):
        if pil_image is None:
            return
        if self._backend is None:
            self.LCD_Init()

        now = time.monotonic()
        if self._min_interval and (now - self._last_push) < self._min_interval:
            return

        if self._push_failed:
            return

        frame = self._prepare_frame(pil_image, x=x, y=y)
        try:
            if self._backend == "lib":
                self._present_lib(frame)
            else:
                self._present_raw(frame)
        except Exception as exc:
            self._push_failed = True
            print(f"⚠️  LCD_ST7789 push failed ({self._backend}): {exc!r}")
            return

        self._last_push = now

    def LCD_Clear(self):
        if self._backend is None:
            self.LCD_Init()
        self.LCD_ShowImage(self._blank, 0, 0)
