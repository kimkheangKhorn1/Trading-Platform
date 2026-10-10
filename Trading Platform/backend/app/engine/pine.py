"""Pine Script value helpers: ``na`` handling and colors.

Colors are carried as ``#RRGGBBAA`` strings. Pine expresses opacity as *transparency* 0..100
(100 = invisible); :func:`rgb` and :func:`new_color` convert exactly like ``color.rgb`` /
``color.new`` do.
"""
from __future__ import annotations

import math

NA = math.nan


def na(x) -> bool:
    return x is None or (isinstance(x, float) and math.isnan(x))


def nz(x, replacement: float = 0.0):
    return replacement if na(x) else x


def _alpha_from_transp(transp: float) -> int:
    transp = min(100.0, max(0.0, float(transp)))
    return int(round(255 * (1 - transp / 100.0)))


def rgb(r: int, g: int, b: int, transp: float = 0) -> str:
    return f"#{int(r):02X}{int(g):02X}{int(b):02X}{_alpha_from_transp(transp):02X}"


def parse_color(color: str) -> tuple[int, int, int, int]:
    c = color.lstrip("#")
    if len(c) == 6:
        c += "FF"
    if len(c) != 8:
        raise ValueError(f"invalid color {color!r}")
    return int(c[0:2], 16), int(c[2:4], 16), int(c[4:6], 16), int(c[6:8], 16)


def new_color(color: str, transp: float) -> str:
    """Equivalent of ``color.new(color, transp)``."""
    r, g, b, _ = parse_color(color)
    return rgb(r, g, b, transp)


def normalize_color(color: str) -> str:
    r, g, b, a = parse_color(color)
    return f"#{r:02X}{g:02X}{b:02X}{a:02X}"


# Pine's named colors (v5 palette)
COLORS = {
    "aqua": "#00BCD4FF", "black": "#363A45FF", "blue": "#2196F3FF", "fuchsia": "#E040FBFF",
    "gray": "#787B86FF", "green": "#4CAF50FF", "lime": "#00E676FF", "maroon": "#880E4FFF",
    "navy": "#311B92FF", "olive": "#808000FF", "orange": "#FF9800FF", "purple": "#9C27B0FF",
    "red": "#FF5252FF", "silver": "#B2B5BEFF", "teal": "#00897BFF", "white": "#FFFFFFFF",
    "yellow": "#FFEB3BFF",
}
