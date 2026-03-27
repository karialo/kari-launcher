from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Mapping


Color = tuple[int, int, int]


@dataclass(frozen=True)
class Theme:
    name: str
    bg: Color
    panel_bg: Color
    panel_border: Color
    text: Color
    dim_text: Color
    neon_primary: Color
    neon_secondary: Color
    ok: Color
    warn: Color
    bad: Color

    def color(self, name: str) -> Color:
        alias = {
            "accent": "neon_primary",
            "muted": "dim_text",
            "err": "bad",
            "border": "panel_border",
            "card": "panel_bg",
        }
        key = alias.get(name, name)
        if not hasattr(self, key):
            return self.text
        value = getattr(self, key)
        if isinstance(value, tuple) and len(value) == 3:
            return value
        return self.text

    def apply_alpha(self, color: Color, a: int) -> tuple[int, int, int, int]:
        alpha = max(0, min(255, int(a)))
        return (int(color[0]), int(color[1]), int(color[2]), alpha)


THEME_ORDER = ["NEON_GREEN", "CYAN", "MAGENTA", "AMBER"]
DEFAULT_THEME_NAME = "NEON_GREEN"

THEMES: Mapping[str, Theme] = {
    "NEON_GREEN": Theme(
        name="NEON_GREEN",
        bg=(7, 15, 12),
        panel_bg=(15, 32, 24),
        panel_border=(74, 186, 126),
        text=(226, 255, 237),
        dim_text=(152, 214, 177),
        neon_primary=(82, 255, 166),
        neon_secondary=(33, 130, 92),
        ok=(106, 244, 140),
        warn=(255, 214, 122),
        bad=(255, 110, 122),
    ),
    "CYAN": Theme(
        name="CYAN",
        bg=(8, 13, 18),
        panel_bg=(14, 27, 38),
        panel_border=(86, 173, 209),
        text=(229, 246, 255),
        dim_text=(160, 209, 227),
        neon_primary=(97, 227, 255),
        neon_secondary=(47, 109, 133),
        ok=(114, 239, 182),
        warn=(255, 213, 118),
        bad=(255, 120, 132),
    ),
    "MAGENTA": Theme(
        name="MAGENTA",
        bg=(17, 9, 18),
        panel_bg=(34, 18, 37),
        panel_border=(176, 92, 206),
        text=(255, 234, 253),
        dim_text=(224, 168, 220),
        neon_primary=(251, 117, 255),
        neon_secondary=(120, 56, 136),
        ok=(123, 242, 170),
        warn=(255, 210, 122),
        bad=(255, 126, 151),
    ),
    "AMBER": Theme(
        name="AMBER",
        bg=(18, 13, 8),
        panel_bg=(36, 26, 16),
        panel_border=(196, 140, 72),
        text=(255, 242, 221),
        dim_text=(224, 191, 149),
        neon_primary=(255, 176, 74),
        neon_secondary=(140, 88, 40),
        ok=(134, 235, 161),
        warn=(255, 206, 110),
        bad=(255, 126, 117),
    ),
}


def load_theme_name_from_env() -> str:
    raw = os.environ.get("LAUNCHER_THEME", DEFAULT_THEME_NAME)
    name = str(raw).strip().upper()
    if name in THEMES:
        return name
    return DEFAULT_THEME_NAME


def load_theme_from_env() -> Theme:
    return THEMES[load_theme_name_from_env()]


def next_theme_name(current: str) -> str:
    cur = (current or "").strip().upper()
    if cur not in THEME_ORDER:
        return DEFAULT_THEME_NAME
    idx = THEME_ORDER.index(cur)
    return THEME_ORDER[(idx + 1) % len(THEME_ORDER)]

