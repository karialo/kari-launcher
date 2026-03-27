from __future__ import annotations

import math
import random
import time

import pygame

from .theme import Theme
from .ui_primitives import GlowCache


def _mix(a: tuple[int, int, int], b: tuple[int, int, int], t: float) -> tuple[int, int, int]:
    return (
        int(a[0] + ((b[0] - a[0]) * t)),
        int(a[1] + ((b[1] - a[1]) * t)),
        int(a[2] + ((b[2] - a[2]) * t)),
    )


class HudEffects:
    def __init__(
        self,
        size: tuple[int, int],
        theme: Theme,
        glow_cache: GlowCache,
        scanlines_enabled: bool = True,
        vignette_enabled: bool = True,
        noise_enabled: bool = False,
    ) -> None:
        self.width, self.height = size
        self.theme = theme
        self.glow_cache = glow_cache
        self.scanlines_enabled = scanlines_enabled
        self.vignette_enabled = vignette_enabled
        self.noise_enabled = noise_enabled
        self._scanlines = self._build_scanlines() if self.scanlines_enabled else None
        self._vignette = self._build_vignette() if self.vignette_enabled else None
        self._noise = self._build_noise() if self.noise_enabled else None
        self._noise_ts = 0.0

    def set_theme(self, theme: Theme) -> None:
        self.theme = theme
        self._scanlines = self._build_scanlines() if self.scanlines_enabled else None
        self._vignette = self._build_vignette() if self.vignette_enabled else None
        if self.noise_enabled:
            self._noise = self._build_noise()

    def _build_scanlines(self) -> pygame.Surface:
        s = pygame.Surface((self.width, self.height), pygame.SRCALPHA)
        for y in range(0, self.height, 2):
            pygame.draw.line(s, (0, 0, 0, 22), (0, y), (self.width, y))
        for y in range(1, self.height, 6):
            pygame.draw.line(
                s,
                (self.theme.neon_secondary[0], self.theme.neon_secondary[1], self.theme.neon_secondary[2], 7),
                (0, y),
                (self.width, y),
            )
        return s

    def _build_vignette(self) -> pygame.Surface:
        s = pygame.Surface((self.width, self.height), pygame.SRCALPHA)
        max_dim = max(self.width, self.height)
        center = (self.width // 2, self.height // 2)
        for i in range(max_dim // 2, 0, -6):
            alpha = int((1.0 - (i / (max_dim / 2))) * 12)
            pygame.draw.circle(s, (0, 0, 0, max(0, min(100, alpha))), center, i, width=8)
        edge = pygame.Surface((self.width, self.height), pygame.SRCALPHA)
        edge.fill((0, 0, 0, 34))
        inner = pygame.Rect(10, 10, self.width - 20, self.height - 20)
        pygame.draw.rect(edge, (0, 0, 0, 0), inner)
        s.blit(edge, (0, 0))
        return s

    def _build_noise(self) -> pygame.Surface:
        s = pygame.Surface((self.width, self.height), pygame.SRCALPHA)
        count = max(120, (self.width * self.height) // 220)
        for _ in range(count):
            x = random.randrange(0, self.width)
            y = random.randrange(0, self.height)
            a = random.randrange(8, 18)
            v = random.randrange(70, 130)
            s.set_at((x, y), (v, v, v, a))
        return s

    def draw_background(self, surface: pygame.Surface, now_s: float, anim_enabled: bool = True) -> None:
        top = self.theme.bg
        bottom = _mix(self.theme.bg, self.theme.neon_secondary, 0.28)
        for y in range(self.height):
            t = y / float(max(1, self.height - 1))
            c = _mix(top, bottom, t)
            pygame.draw.line(surface, c, (0, y), (self.width, y))

        if not anim_enabled:
            return

        t = now_s * 1.35
        # Keep the moving circles from the previous UI, but style as neon rings.
        p1 = (int(self.width * 0.22 + math.sin(t) * 20), int(self.height * 0.20 + math.cos(t * 0.8) * 10))
        p2 = (int(self.width * 0.78 + math.cos(t * 1.1) * 18), int(self.height * 0.78 + math.sin(t * 0.7) * 12))
        p3 = (int(self.width * 0.50 + math.sin(t * 0.55) * 16), int(self.height * 0.50 + math.cos(t * 0.62) * 10))

        ring1 = self.glow_cache.ring(38, 2, self.theme.neon_primary)
        ring2 = self.glow_cache.ring(44, 2, self.theme.neon_secondary)
        ring3 = self.glow_cache.ring(30, 1, self.theme.neon_primary)

        surface.blit(ring1, (p1[0] - (ring1.get_width() // 2), p1[1] - (ring1.get_height() // 2)))
        surface.blit(ring2, (p2[0] - (ring2.get_width() // 2), p2[1] - (ring2.get_height() // 2)))
        surface.blit(ring3, (p3[0] - (ring3.get_width() // 2), p3[1] - (ring3.get_height() // 2)))

    def draw_overlays(self, surface: pygame.Surface, now_s: float) -> None:
        if self.scanlines_enabled and self._scanlines is not None:
            shift = int((now_s * 8.0) % 4)
            surface.blit(self._scanlines, (0, shift - 2))
        if self.noise_enabled:
            if (now_s - self._noise_ts) >= 0.5:
                self._noise = self._build_noise()
                self._noise_ts = now_s
            if self._noise is not None:
                surface.blit(self._noise, (0, 0))
        if self.vignette_enabled and self._vignette is not None:
            surface.blit(self._vignette, (0, 0))
