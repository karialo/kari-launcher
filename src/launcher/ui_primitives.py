from __future__ import annotations

import math
import os
from dataclasses import dataclass
from typing import Any

import pygame

from .theme import Color, Theme


@dataclass(frozen=True)
class PanelStyle:
    radius: int = 0
    border_width: int = 1
    inner_border_alpha: int = 76
    grid_alpha: int = 22
    grid_step: int = 14
    chamfer: int = 10
    accent_len: int = 22
    accent_width: int = 2


class TextRenderer:
    def __init__(self, font_paths: list[str] | None = None) -> None:
        self.font_paths = font_paths or [
            "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
            "/usr/share/fonts/truetype/freefont/FreeSans.ttf",
        ]
        self._font_cache: dict[int, pygame.font.Font] = {}
        self._surface_cache: dict[tuple[int, str, Color], pygame.Surface] = {}

    def _font(self, size: int) -> pygame.font.Font:
        size = max(8, int(size))
        if size in self._font_cache:
            return self._font_cache[size]
        for path in self.font_paths:
            if os.path.exists(path):
                self._font_cache[size] = pygame.font.Font(path, size)
                return self._font_cache[size]
        self._font_cache[size] = pygame.font.SysFont(None, size)
        return self._font_cache[size]

    def render(self, text: str, size: int, color: Color) -> pygame.Surface:
        safe = str(text)
        key = (int(size), safe, (int(color[0]), int(color[1]), int(color[2])))
        cached = self._surface_cache.get(key)
        if cached is not None:
            return cached
        surf = self._font(size).render(safe, True, color)
        self._surface_cache[key] = surf
        return surf

    def blit(self, surface: pygame.Surface, text: str, size: int, color: Color, pos: tuple[int, int]) -> pygame.Rect:
        s = self.render(text, size, color)
        return surface.blit(s, pos)

    def clear_text_cache(self) -> None:
        self._surface_cache.clear()


class GlowCache:
    def __init__(self) -> None:
        self._dot_cache: dict[tuple[int, Color], pygame.Surface] = {}
        self._ring_cache: dict[tuple[int, int, Color], pygame.Surface] = {}
        self._line_cache: dict[tuple[int, int, Color], pygame.Surface] = {}
        self._grid_cache: dict[tuple[int, int, Color, int], pygame.Surface] = {}

    def dot(self, radius: int, color: Color) -> pygame.Surface:
        key = (int(radius), color)
        cached = self._dot_cache.get(key)
        if cached is not None:
            return cached
        r = max(2, int(radius))
        size = (r * 4) + 2
        cx = size // 2
        surf = pygame.Surface((size, size), pygame.SRCALPHA)
        for scale, alpha in ((1.9, 26), (1.45, 52), (1.0, 140)):
            pygame.draw.circle(surf, (color[0], color[1], color[2], alpha), (cx, cx), int(r * scale))
        self._dot_cache[key] = surf
        return surf

    def ring(self, radius: int, thickness: int, color: Color) -> pygame.Surface:
        key = (int(radius), int(thickness), color)
        cached = self._ring_cache.get(key)
        if cached is not None:
            return cached
        r = max(8, int(radius))
        w = max(1, int(thickness))
        size = (r * 4) + 2
        cx = size // 2
        surf = pygame.Surface((size, size), pygame.SRCALPHA)
        for spread, alpha in ((5, 16), (3, 28), (1, 56)):
            pygame.draw.circle(surf, (color[0], color[1], color[2], alpha), (cx, cx), r + spread, w + spread)
        pygame.draw.circle(surf, (color[0], color[1], color[2], 104), (cx, cx), r, w)
        self._ring_cache[key] = surf
        return surf

    def line(self, length: int, thickness: int, color: Color) -> pygame.Surface:
        key = (int(length), int(thickness), color)
        cached = self._line_cache.get(key)
        if cached is not None:
            return cached
        l = max(4, int(length))
        t = max(1, int(thickness))
        surf = pygame.Surface((l + 8, (t * 8) + 2), pygame.SRCALPHA)
        cy = surf.get_height() // 2
        for spread, alpha in ((4, 20), (2, 40), (1, 84)):
            pygame.draw.line(
                surf,
                (color[0], color[1], color[2], alpha),
                (4, cy),
                (l + 4, cy),
                max(1, t + spread),
            )
        pygame.draw.line(surf, (color[0], color[1], color[2], 128), (4, cy), (l + 4, cy), t)
        self._line_cache[key] = surf
        return surf

    def grid(self, size: tuple[int, int], color: Color, step: int = 12) -> pygame.Surface:
        w, h = size
        key = (int(w), int(h), color, int(step))
        cached = self._grid_cache.get(key)
        if cached is not None:
            return cached
        surf = pygame.Surface((w, h), pygame.SRCALPHA)
        a_minor = 18
        a_major = 30
        for x in range(0, w, step):
            alpha = a_major if (x // step) % 4 == 0 else a_minor
            pygame.draw.line(surf, (color[0], color[1], color[2], alpha), (x, 0), (x, h))
        for y in range(0, h, step):
            alpha = a_major if (y // step) % 4 == 0 else a_minor
            pygame.draw.line(surf, (color[0], color[1], color[2], alpha), (0, y), (w, y))
        self._grid_cache[key] = surf
        return surf


def _panel_points(width: int, height: int, chamfer: int, inset: int = 0) -> list[tuple[int, int]]:
    left = max(0, int(inset))
    top = max(0, int(inset))
    right = max(left + 2, int(width) - 1 - int(inset))
    bottom = max(top + 2, int(height) - 1 - int(inset))
    c = max(3, int(chamfer))
    c = min(c, max(3, (right - left) // 3), max(3, (bottom - top) // 3))
    return [
        (left + c, top),
        (right - c, top),
        (right, top + c),
        (right, bottom - c),
        (right - c, bottom),
        (left + c, bottom),
        (left, bottom - c),
        (left, top + c),
    ]


def draw_panel(
    surface: pygame.Surface,
    rect: pygame.Rect,
    theme: Theme,
    glow_cache: GlowCache,
    title: str | None = None,
    text_renderer: TextRenderer | None = None,
    style: PanelStyle | None = None,
    alt: bool = False,
) -> None:
    pstyle = style or PanelStyle()
    if rect.width < 12 or rect.height < 12:
        return

    w = int(rect.width)
    h = int(rect.height)
    panel = pygame.Surface((w, h), pygame.SRCALPHA)

    fill = theme.panel_bg
    if alt:
        fill = (
            max(0, min(255, fill[0] + 7)),
            max(0, min(255, fill[1] + 7)),
            max(0, min(255, fill[2] + 7)),
        )
    pts = _panel_points(w, h, pstyle.chamfer, inset=0)
    pygame.draw.polygon(panel, (fill[0], fill[1], fill[2], 245), pts)

    if w > 24 and h > 24:
        grid = glow_cache.grid((w, h), theme.neon_secondary, pstyle.grid_step).copy()
        grid.set_alpha(pstyle.grid_alpha)
        panel.blit(grid, (0, 0))
        mask = pygame.Surface((w, h), pygame.SRCALPHA)
        pygame.draw.polygon(mask, (255, 255, 255, 255), pts)
        panel.blit(mask, (0, 0), special_flags=pygame.BLEND_RGBA_MULT)

    pygame.draw.polygon(panel, theme.panel_border, pts, width=max(1, int(pstyle.border_width)))

    inner_pts = _panel_points(w, h, max(4, pstyle.chamfer - 3), inset=5)
    pygame.draw.polygon(
        panel,
        (theme.panel_border[0], theme.panel_border[1], theme.panel_border[2], pstyle.inner_border_alpha),
        inner_pts,
        width=1,
    )

    accent = theme.neon_secondary if alt else theme.neon_primary
    a_len = max(12, int(pstyle.accent_len))
    a_w = max(1, int(pstyle.accent_width))
    c = max(4, min(int(pstyle.chamfer), w // 3, h // 3))

    pygame.draw.line(panel, accent, (c + 2, 2), (min(w - 4, c + a_len), 2), a_w)
    pygame.draw.line(panel, accent, (w - c - 2, 2), (max(2, w - c - a_len), 2), a_w)
    pygame.draw.line(panel, accent, (2, c + 2), (2, min(h - 4, c + a_len)), a_w)
    pygame.draw.line(panel, accent, (w - 3, h - c - 2), (w - 3, max(2, h - c - a_len)), a_w)
    pygame.draw.line(panel, accent, (c + 2, h - 3), (min(w - 4, c + a_len + 6), h - 3), a_w)

    pygame.draw.circle(panel, accent, (c + 2, c + 2), 1)
    pygame.draw.circle(panel, accent, (w - c - 3, c + 2), 1)

    surface.blit(panel, rect.topleft)

    if title and text_renderer is not None:
        accent = theme.neon_secondary if alt else theme.neon_primary
        text_renderer.blit(surface, title, 14, accent, (rect.x + 12, rect.y + 8))
        bar = glow_cache.line(max(24, rect.width - 108), 1, accent)
        surface.blit(bar, (rect.x + 78, rect.y + 15))
        pygame.draw.circle(surface, accent, (rect.x + 72, rect.y + 16), 2)


def draw_status_dot(
    surface: pygame.Surface,
    center: tuple[int, int],
    state: str,
    theme: Theme,
    glow_cache: GlowCache,
    now_s: float,
    anim_enabled: bool = True,
) -> None:
    if state == "online":
        color = theme.ok
        hz = 0.9
    elif state == "degraded":
        color = theme.warn
        hz = 1.35
    elif state == "offline":
        color = theme.bad
        hz = 2.2
    else:
        color = theme.dim_text
        hz = 0.7

    base_r = 4
    pulse = 0.0
    if anim_enabled:
        pulse = (math.sin(now_s * math.tau * hz) + 1.0) * 0.5
    r = base_r + int(pulse * 2)
    dot = glow_cache.dot(r, color)
    surface.blit(dot, (center[0] - (dot.get_width() // 2), center[1] - (dot.get_height() // 2)))
    pygame.draw.circle(surface, color, center, r)


def tint_icon(icon: pygame.Surface, tint: Color, alpha: int = 180) -> pygame.Surface:
    out = icon.copy().convert_alpha()
    overlay = pygame.Surface(out.get_size(), pygame.SRCALPHA)
    overlay.fill((tint[0], tint[1], tint[2], max(0, min(255, int(alpha)))))
    out.blit(overlay, (0, 0), special_flags=pygame.BLEND_RGBA_ADD)
    return out
