from __future__ import annotations

import time
from dataclasses import dataclass

import pygame


def ease_out_cubic(t: float) -> float:
    t = max(0.0, min(1.0, t))
    return 1.0 - ((1.0 - t) ** 3)


@dataclass
class PageTransition:
    duration_s: float = 0.25
    enabled: bool = True
    active: bool = False
    start_ts: float = 0.0
    direction: int = 1
    prev_frame: pygame.Surface | None = None
    next_frame: pygame.Surface | None = None

    @staticmethod
    def _blit_scaled(
        target: pygame.Surface,
        source: pygame.Surface,
        x: int,
        y: int,
        scale: float,
        alpha: int,
    ) -> None:
        s = max(0.75, min(1.2, float(scale)))
        a = max(0, min(255, int(alpha)))
        w, h = source.get_size()
        nw = max(1, int(w * s))
        nh = max(1, int(h * s))
        if nw == w and nh == h:
            frame = source.copy()
        else:
            frame = pygame.transform.smoothscale(source, (nw, nh))
        if a < 255:
            frame.set_alpha(a)
        target.blit(frame, (x + ((w - nw) // 2), y + ((h - nh) // 2)))

    def start(self, prev_frame: pygame.Surface, next_frame: pygame.Surface, direction: int = 1) -> None:
        if not self.enabled:
            self.active = False
            self.prev_frame = None
            self.next_frame = next_frame
            return
        self.prev_frame = prev_frame.copy()
        self.next_frame = next_frame.copy()
        self.direction = -1 if direction < 0 else 1
        self.start_ts = time.monotonic()
        self.active = True

    def clear(self) -> None:
        self.active = False
        self.prev_frame = None
        self.next_frame = None

    def compose_into(self, target: pygame.Surface, now_ts: float | None = None) -> bool:
        if not self.active or self.prev_frame is None or self.next_frame is None:
            if self.next_frame is not None:
                target.blit(self.next_frame, (0, 0))
                return False
            return False

        now = time.monotonic() if now_ts is None else now_ts
        elapsed = max(0.0, now - self.start_ts)
        progress = min(1.0, elapsed / max(0.001, self.duration_s))
        eased = ease_out_cubic(progress)
        width = target.get_width()
        slide = int(width * eased)
        frame = pygame.Surface(target.get_size(), pygame.SRCALPHA)
        prev_scale = 1.0 - (0.08 * eased)
        next_scale = 0.92 + (0.08 * eased)
        prev_alpha = int(255 * (1.0 - (0.35 * eased)))
        next_alpha = int(180 + (75 * eased))
        if self.direction >= 0:
            self._blit_scaled(frame, self.prev_frame, -slide, 0, prev_scale, prev_alpha)
            self._blit_scaled(frame, self.next_frame, width - slide, 0, next_scale, next_alpha)
        else:
            self._blit_scaled(frame, self.prev_frame, slide, 0, prev_scale, prev_alpha)
            self._blit_scaled(frame, self.next_frame, -width + slide, 0, next_scale, next_alpha)
        target.blit(frame, (0, 0))

        if progress >= 1.0:
            self.active = False
            self.prev_frame = None
            return False
        return True
