"""Pine built-ins shared by the converted TradingView scripts: number formatting (``str.tostring``), colors
(``color.from_gradient`` / ``color.t``), ``ta.pivothigh`` / ``ta.pivotlow`` and ``array.binary_search_leftmost``."""
from __future__ import annotations

import math
from bisect import bisect_left
from collections import deque
from decimal import ROUND_HALF_UP, Decimal

from ..engine.pine import parse_color, rgb

NAN = math.nan
# Chart colors Pine reports as chart.bg_color / chart.fg_color (the app's dark chart, see frontend index.css)
CHART_BG = '#090D14FF'
CHART_FG = '#D1D4DCFF'
GRAY = '#787B86FF'  # color.gray
WHITE = '#FFFFFFFF'


def finite(x) -> bool:
    return x is not None and isinstance(x, (int, float)) and math.isfinite(x)


def _round(x: float, decimals: int) -> Decimal:
    return Decimal(repr(float(x))).quantize(Decimal(1).scaleb(-decimals), rounding=ROUND_HALF_UP)


def fmt_pattern(x: float, pattern: str) -> str:
    """``str.tostring(x, "#.##")`` style patterns: ``0`` digits are always shown, ``#`` digits only when not zero."""
    if not finite(x):
        return 'NaN'
    frac = pattern.split('.', 1)[1] if '.' in pattern else ''
    d = _round(x, len(frac))
    neg = d < 0
    whole, _, part = f'{abs(d):f}'.partition('.')
    part = part.ljust(len(frac), '0')
    keep = len(frac.rstrip('#'))  # mandatory decimals
    part = part[:keep] + part[keep:].rstrip('0')
    s = whole + ('.' + part if part else '')
    return ('-' if neg and s.strip('0.') else '') + s


def fmt_price(x: float, precision: int) -> str:
    """``str.tostring(x, format.mintick)``: rounded to the tick, with the symbol's decimals."""
    if not finite(x):
        return 'NaN'
    return f'{_round(x, precision):f}'


def fmt_volume(x: float) -> str:
    """``format.volume``: K / M / B suffixes, at most two decimals."""
    if not finite(x):
        return 'NaN'
    a = abs(x)
    for div, suffix in ((1e12, 'T'), (1e9, 'B'), (1e6, 'M'), (1e3, 'K')):
        if a >= div:
            return fmt_pattern(x / div, '#.##') + suffix
    return fmt_pattern(x, '#.##')


def fmt_percent(x: float) -> str:
    """``format.percent``: two decimals and a percent sign."""
    if not finite(x):
        return 'NaN'
    return fmt_pattern(x, '0.00') + '%'


def tostring(x) -> str:
    if isinstance(x, bool):
        return 'true' if x else 'false'
    if isinstance(x, int):
        return str(x)
    if not finite(x):
        return 'NaN'
    return fmt_pattern(x, '#.##########')


def color_t(color: str) -> float:
    """``color.t``: transparency 0..100 of a ``#RRGGBBAA`` color."""
    return (1 - parse_color(color)[3] / 255) * 100


def with_alpha_of(color: str, transp: float) -> str:
    r, g, b, _ = parse_color(color)
    return rgb(r, g, b, transp)


def from_gradient(value: float, bottom: float, top: float, bottom_color: str, top_color: str) -> str | None:
    """``color.from_gradient``: linear blend of both colors (and transparencies), clamped to the range."""
    if not (finite(value) and finite(bottom) and finite(top)):
        return None
    if top == bottom:
        f = 0.0 if value <= bottom else 1.0
    else:
        f = min(1.0, max(0.0, (value - bottom) / (top - bottom)))
    a, b = parse_color(bottom_color), parse_color(top_color)
    r, g, bl, al = (a[k] + (b[k] - a[k]) * f for k in range(4))
    return f'#{round(r):02X}{round(g):02X}{round(bl):02X}{round(al):02X}'


def binary_search_leftmost(arr: list[float], value: float) -> int:
    """``array.binary_search_leftmost`` on an ascending array: the index of ``value`` if present (first one),
    otherwise the index of the next smaller element (-1 if there is none)."""
    i = bisect_left(arr, value)
    if i < len(arr) and arr[i] == value:
        return i
    return i - 1


class Pivot:
    """``ta.pivothigh`` / ``ta.pivotlow``: the value ``right`` bars back when it is at least as extreme as the
    ``left`` bars before it and strictly more extreme than the ``right`` bars after it (Pine keeps the last of
    equal extremes); na otherwise."""

    def __init__(self, left: int, right: int, high: bool):
        self.left, self.right, self.high = left, right, high
        self.buf: deque[float] = deque(maxlen=left + right + 1)

    def update(self, x: float) -> float:
        self.buf.append(float(x))
        if len(self.buf) < self.left + self.right + 1:
            return NAN
        a = list(self.buf)
        c = a[self.left]
        if not finite(c):
            return NAN
        if self.high:
            ok = all(c >= v for v in a[:self.left]) and all(c > v for v in a[self.left + 1:])
        else:
            ok = all(c <= v for v in a[:self.left]) and all(c < v for v in a[self.left + 1:])
        return c if ok else NAN


TABLE_POSITIONS = {
    'Top Left': 'top_left', 'Top Right': 'top_right', 'Bottom Left': 'bottom_left', 'Bottom Right': 'bottom_right',
    'Top Center': 'top_center', 'Bottom Center': 'bottom_center', 'Middle Left': 'middle_left',
    'Middle Right': 'middle_right', 'Middle Center': 'middle_center',
}


class Table:
    """A Pine ``table``: cells set by (column, row); rows/columns never set are not shown (as on TradingView)."""

    def __init__(self, tid: str, position: str, columns: int, rows: int, *, bg_color: str | None = None,
                 frame_color: str | None = None, frame_width: int = 0, border_color: str | None = None,
                 border_width: int = 0):
        self.id, self.position, self.columns, self.rows = tid, position, columns, rows
        self.style = dict(bg_color=bg_color, frame_color=frame_color, frame_width=frame_width,
                          border_color=border_color, border_width=border_width)
        self.cells: dict[tuple[int, int], dict] = {}

    def cell(self, column: int, row: int, text: str = '', *, text_color: str = '#000000FF', bgcolor: str | None = None,
             text_size: str = 'normal', text_halign: str = 'center', text_valign: str = 'center', tooltip: str = ''):
        self.cells[(column, row)] = dict(text=text, text_color=text_color or '#00000000',
                                         bg_color=bgcolor or '#00000000', text_size=text_size,
                                         text_halign=text_halign, text_valign=text_valign, tooltip=tooltip)

    def to_json(self) -> dict | None:
        if not self.cells:
            return None
        cols = range(max(c for c, _ in self.cells) + 1)
        rows = range(max(r for _, r in self.cells) + 1)
        empty = dict(text='', text_color='#00000000', bg_color='#00000000', tooltip='', text_size='normal')
        return dict(id=self.id, position=self.position, **self.style,
                    rows=[[self.cells.get((c, r), empty) for c in cols] for r in rows])
