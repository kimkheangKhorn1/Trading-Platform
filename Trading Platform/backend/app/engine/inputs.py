"""Indicator input definitions mirroring Pine's ``input.*`` functions.

Each script declares a list of :class:`InputSpec`. The API exposes them to the settings dialog
(Inputs tab, grouped by ``group``/``inline`` exactly like TradingView) and every value sent back is
validated here before a calculation runs - unknown keys or out-of-range values are rejected rather
than silently clamped, so a calculation never runs with settings the user did not choose.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Literal

from .pine import normalize_color

InputType = Literal["int", "float", "bool", "string", "color", "source", "timeframe"]
SOURCES = ("open", "high", "low", "close", "hl2", "hlc3", "ohlc4", "hlcc4")
TIMEFRAME_VALUES = ("", "1", "5", "15", "30", "60", "240", "D")  # '' = chart timeframe


class InputError(ValueError):
    pass


@dataclass(frozen=True)
class InputSpec:
    id: str
    type: InputType
    title: str
    default: Any
    minval: float | None = None
    maxval: float | None = None
    step: float | None = None
    options: tuple | None = None
    group: str | None = None
    inline: str | None = None
    tooltip: str | None = None
    # plain-language help for the settings dialog: what the input does, and a worked example
    help: str | None = None
    example: str | None = None
    # earlier defaults of this input: saved settings still holding one are moved to the current default
    replaced_defaults: tuple | None = None

    def validate(self, value: Any) -> Any:
        t = self.type
        try:
            if t == "int":
                if isinstance(value, bool) or (isinstance(value, float) and not value.is_integer()):
                    raise InputError(f"{self.title}: expected an integer")
                v: Any = int(value)
            elif t == "float":
                if isinstance(value, bool):
                    raise InputError(f"{self.title}: expected a number")
                v = float(value)
            elif t == "bool":
                if not isinstance(value, bool):
                    raise InputError(f"{self.title}: expected true/false")
                v = value
            elif t == "color":
                v = normalize_color(str(value))
            elif t == "source":
                v = str(value)
                if v not in SOURCES:
                    raise InputError(f"{self.title}: unknown source {v!r}")
            elif t == "timeframe":
                v = str(value)
                if v not in TIMEFRAME_VALUES:
                    raise InputError(f"{self.title}: unsupported timeframe {v!r}")
            else:
                v = str(value)
        except (TypeError, ValueError) as exc:
            if isinstance(exc, InputError):
                raise
            raise InputError(f"{self.title}: invalid value {value!r}") from exc
        if self.options is not None and v not in self.options:
            raise InputError(f"{self.title}: {v!r} is not one of {list(self.options)}")
        if self.minval is not None and t in ("int", "float") and v < self.minval:
            raise InputError(f"{self.title}: must be >= {self.minval}")
        if self.maxval is not None and t in ("int", "float") and v > self.maxval:
            raise InputError(f"{self.title}: must be <= {self.maxval}")
        return v

    def to_dict(self) -> dict:
        d = asdict(self)
        d["options"] = list(self.options) if self.options is not None else None
        return d


def resolve_inputs(specs: list[InputSpec], values: dict | None) -> dict:
    """Defaults overlaid with validated user values."""
    by_id = {s.id: s for s in specs}
    values = values or {}
    unknown = sorted(set(values) - set(by_id))
    if unknown:
        raise InputError(f"unknown inputs: {unknown}")
    out = {}
    for spec in specs:
        out[spec.id] = spec.validate(values[spec.id]) if spec.id in values else spec.validate(spec.default)
    return out


@dataclass(frozen=True)
class PlotSpec:
    """A ``plot()`` series. Style fields are the defaults shown in the Style tab."""

    id: str
    title: str
    color: str
    linewidth: int = 1
    style: Literal["line", "stepline", "histogram", "circles", "cross", "area"] = "line"
    linestyle: Literal["solid", "dashed", "dotted"] = "solid"
    pane: Literal["overlay", "separate"] = "overlay"
    visible: bool = True
    replaced_colors: tuple | None = None  # earlier default colors: a saved style still at one moves to `color` once

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass(frozen=True)
class ShapeSpec:
    """A ``plotshape()`` series (one marker per bar where the condition is true)."""

    id: str
    title: str
    shape: Literal["triangleup", "triangledown", "arrowup", "arrowdown", "circle", "square", "diamond",
                   "cross", "xcross", "flag", "labelup", "labeldown"]
    location: Literal["abovebar", "belowbar", "absolute"]
    color: str
    text: str = ""
    textcolor: str = "#FFFFFFFF"
    size: Literal["tiny", "small", "normal", "large", "huge", "auto"] = "small"
    visible: bool = True
    replaced_colors: tuple | None = None  # earlier default colors: a saved style still at one moves to `color` once

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass(frozen=True)
class StrategySpec:
    """A named trade-signal source emitted by a script (a script may contain several)."""

    id: str
    title: str
    description: str = ""

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass(frozen=True)
class DrawingGroup:
    """A category of dynamic drawings (boxes/lines/labels) that can be toggled in the Style tab."""

    id: str
    title: str
    visible: bool = True

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class ScriptMeta:
    id: str
    name: str
    short_name: str
    overlay: bool = True
    description: str = ""
    category: str = "Example"
    max_boxes: int = 500
    max_lines: int = 500
    max_labels: int = 500
    inputs: list[InputSpec] = field(default_factory=list)
    plots: list[PlotSpec] = field(default_factory=list)
    shapes: list[ShapeSpec] = field(default_factory=list)
    strategies: list[StrategySpec] = field(default_factory=list)
    drawing_groups: list[DrawingGroup] = field(default_factory=list)
    # drawing groups with their own object quota, e.g. {"trades": {"box": 5000, "line": 5000, "label": 5000}}
    group_limits: dict[str, dict[str, int]] = field(default_factory=dict)
    # Pine ``indicator(behind_chart=...)``: False draws the script's boxes in front of the candles
    behind_chart: bool = True
    # False keeps labels and markers exactly where the script puts them, overlapping like on TradingView; True lets
    # the chart move them apart (with a connector to their anchor) when they collide
    label_layout: bool = True
    # inputs that only change what is drawn (Script.display_filter): calculations, caches and overlap scans ignore them
    display_inputs: tuple[str, ...] = ()

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "name": self.name,
            "short_name": self.short_name,
            "overlay": self.overlay,
            "description": self.description,
            "category": self.category,
            "inputs": [i.to_dict() for i in self.inputs],
            "plots": [p.to_dict() for p in self.plots],
            "shapes": [s.to_dict() for s in self.shapes],
            "strategies": [s.to_dict() for s in self.strategies],
            "drawing_groups": [g.to_dict() for g in self.drawing_groups],
            "behind_chart": self.behind_chart,
            "label_layout": self.label_layout,
            "display_inputs": list(self.display_inputs),
        }
