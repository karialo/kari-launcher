#!/usr/bin/env python3
from __future__ import annotations

import os
import re
import subprocess
import textwrap
import time
from pathlib import Path

os.environ.setdefault("SDL_VIDEODRIVER", "dummy")

import pygame

from .dashboard import CONFIG_PATH, WaveshareDisplay, WaveshareInput, clamp, ensure_config


ANSI_RE = re.compile(r"\x1b\[[0-9;?]*[ -/]*[@-~]")
PREFIX_RE = re.compile(r"^\[[^\]]+\]\s*")


def _clean_line(text: str) -> str:
    line = ANSI_RE.sub("", text or "")
    line = line.replace("\r", " ").replace("\x00", " ")
    line = PREFIX_RE.sub("", line)
    line = re.sub(r"\s+", " ", line).strip()
    return line


def _wrap_line(text: str, width: int = 28) -> list[str]:
    cleaned = _clean_line(text)
    if not cleaned:
        return [""]
    return textwrap.wrap(cleaned, width=width, break_long_words=True, break_on_hyphens=False) or [cleaned[:width]]


class TermieApp:
    def __init__(self) -> None:
        self.config = ensure_config(CONFIG_PATH)
        self.log_path = Path(
            str(
                os.environ.get("TERMIE_LOG_PATH")
                or (
                    self.config.get("termie", {}).get("log_path", "/tmp/termie.log")
                    if isinstance(self.config.get("termie"), dict)
                    else "/tmp/termie.log"
                )
            )
        ).expanduser()
        self.exit_cmd = os.environ.get("TERMIE_EXIT_CMD", "").strip()
        self.running = True
        self.display = self._build_display()
        self.input = self._build_input()
        self.screen = pygame.Surface((240, 240))
        self.font = pygame.font.SysFont("DejaVu Sans Mono", 15)
        self.small_font = pygame.font.SysFont("DejaVu Sans Mono", 11)
        self.scroll_offset = 0
        self.status = "waiting for logs"
        self.last_mtime_ns = 0
        self.last_size = -1
        self.last_refresh_ts = 0.0
        self.lines: list[str] = []

    def _build_display(self) -> WaveshareDisplay:
        hw = self.config.get("hardware", {}) if isinstance(self.config.get("hardware"), dict) else {}
        return WaveshareDisplay(
            spi_port=int(hw.get("spi_port", 0)),
            spi_cs=int(hw.get("spi_cs", 0)),
            dc_pin=int(hw.get("dc_pin", 25)),
            rst_pin=int(hw.get("rst_pin", 27)),
            backlight_pin=int(hw.get("backlight_pin", 24)),
            rotation=int(hw.get("rotation", 90)),
            invert=bool(hw.get("invert", True)),
            spi_speed_hz=int(hw.get("spi_speed_hz", 24_000_000)),
        )

    def _build_input(self) -> WaveshareInput | None:
        input_cfg = self.config.get("input", {}) if isinstance(self.config.get("input"), dict) else {}
        pins = input_cfg.get("pins", {}) if isinstance(input_cfg.get("pins"), dict) else {}
        try:
            backend = WaveshareInput(pins, float(input_cfg.get("debounce_seconds", 0.10) or 0.10))
            backend.init()
            return backend
        except Exception:
            return None

    def _update_display(self) -> None:
        self.display.display_surface(self.screen)

    def _load_lines(self) -> None:
        if not self.log_path.exists():
            self.lines = ["(no log file yet)"]
            self.status = f"waiting {self.log_path.name}"
            self.scroll_offset = 0
            return
        try:
            stat = self.log_path.stat()
            self.last_mtime_ns = getattr(stat, "st_mtime_ns", int(stat.st_mtime * 1_000_000_000))
            self.last_size = stat.st_size
            self.last_refresh_ts = time.monotonic()
            raw_lines = self.log_path.read_text(encoding="utf-8", errors="replace").splitlines()
        except Exception as exc:
            self.lines = [f"read failed: {exc}"]
            self.status = "read error"
            return

        wrapped: list[str] = []
        for raw in raw_lines[-300:]:
            wrapped.extend(_wrap_line(raw, 28))
        self.lines = wrapped or ["(log empty)"]
        self.status = self.log_path.name
        max_offset = max(0, len(self.lines) - 10)
        self.scroll_offset = min(self.scroll_offset, max_offset)

    def _draw_fade(self, target_lines: list[str], fade_in: bool) -> None:
        steps = 10
        for idx in range(steps + 1):
            alpha = int((255 * idx) / steps)
            if not fade_in:
                alpha = 255 - alpha
            self._render(target_lines)
            overlay = pygame.Surface(self.screen.get_size(), pygame.SRCALPHA)
            overlay.fill((0, 0, 0, clamp(255 - alpha, 0, 255)))
            self.screen.blit(overlay, (0, 0))
            self._update_display()
            time.sleep(0.03)

    def _render(self, lines: list[str] | None = None) -> None:
        self.screen.fill((0, 0, 0))
        green = (38, 255, 121)
        dim = (110, 180, 130)
        accent = (160, 255, 200)

        pygame.draw.rect(self.screen, green, pygame.Rect(8, 8, 224, 224), width=2, border_radius=6)
        pygame.draw.rect(self.screen, (4, 20, 10), pygame.Rect(12, 12, 216, 216), border_radius=4)

        title = self.font.render("Termie", True, accent)
        self.screen.blit(title, (18, 18))
        clock_text = time.strftime("%H:%M:%S")
        clock = self.small_font.render(clock_text, True, dim)
        self.screen.blit(clock, (self.screen.get_width() - 18 - clock.get_width(), 22))

        status = self.small_font.render(self.status[:24], True, dim)
        self.screen.blit(status, (18, 40))
        age_s = max(0, int(time.monotonic() - self.last_refresh_ts)) if self.last_refresh_ts else -1
        meta_text = f"{len(self.lines)}l upd {age_s}s" if age_s >= 0 else "waiting"
        meta = self.small_font.render(meta_text[:24], True, dim)
        self.screen.blit(meta, (self.screen.get_width() - 18 - meta.get_width(), 40))

        visible = 9
        body = self.lines if lines is None else lines
        start = max(0, len(body) - visible - self.scroll_offset)
        visible_lines = body[start : start + visible]
        y = 62
        for line in visible_lines:
            surf = self.small_font.render(line[:34], True, green)
            self.screen.blit(surf, (18, y))
            y += 16

        footer = "U/D scroll  K3 launcher"
        footer_surf = self.small_font.render(footer, True, dim)
        self.screen.blit(footer_surf, (18, 214))

    def _scroll(self, delta: int) -> None:
        max_offset = max(0, len(self.lines) - 10)
        self.scroll_offset = clamp(self.scroll_offset + delta, 0, max_offset)

    def _request_exit(self) -> None:
        self.running = False
        if self.exit_cmd:
            try:
                subprocess.Popen(
                    ["/usr/bin/env", "bash", "-lc", self.exit_cmd],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    stdin=subprocess.DEVNULL,
                    start_new_session=True,
                )
            except Exception:
                pass

    def run(self) -> int:
        self._load_lines()
        self._draw_fade(self.lines[-10:], fade_in=True)
        clock = pygame.time.Clock()
        while self.running:
            if self.input is not None:
                self.input.poll()
            for event in pygame.event.get():
                if event.type == pygame.QUIT:
                    self.running = False
                elif event.type == pygame.KEYDOWN:
                    if event.key == pygame.K_UP:
                        self._scroll(+1)
                    elif event.key == pygame.K_DOWN:
                        self._scroll(-1)
                    elif event.key == pygame.K_F3:
                        self._request_exit()

            try:
                if self.log_path.exists():
                    stat = self.log_path.stat()
                    mtime_ns = getattr(stat, "st_mtime_ns", int(stat.st_mtime * 1_000_000_000))
                    size = stat.st_size
                    if (mtime_ns != self.last_mtime_ns) or (size != self.last_size):
                        self._load_lines()
            except Exception:
                pass

            self._render()
            self._update_display()
            clock.tick(8)

        self._draw_fade(self.lines[-10:], fade_in=False)
        if self.input is not None:
            self.input.cleanup()
        return 0


def main() -> int:
    pygame.init()
    try:
        app = TermieApp()
        return app.run()
    finally:
        pygame.quit()


if __name__ == "__main__":
    raise SystemExit(main())
