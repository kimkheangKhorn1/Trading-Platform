"""Dynamic drawing objects (Pine ``box``, ``line`` and ``label``) and their store.

Coordinates are bar *open times* (UTC seconds) on the x axis and prices on the y axis, so the
rendering layer can place them without knowing the engine's bar indexing. Each object records the
time of the bar on which the script created it (``created_at``) - the moment the information became
known - which can differ from its anchor (e.g. a swing label anchored ``n`` bars back).

Like Pine, each object type has a maximum live count; when a script exceeds it the oldest objects
are deleted (``max_boxes_count`` / ``max_lines_count`` / ``max_labels_count`` semantics).
"""
from __future__ import annotations

from collections import OrderedDict
from dataclasses import asdict, dataclass, field
from typing import Literal

LineStyle = Literal["solid", "dashed", "dotted", "arrow_left", "arrow_right", "arrow_both"]
Extend = Literal["none", "left", "right", "both"]


@dataclass
class Box:
    id: int
    left: int
    right: int
    top: float
    bottom: float
    border_color: str = "#2962FFFF"
    border_width: int = 1
    border_style: LineStyle = "solid"
    bg_color: str = "#2962FF33"
    extend: Extend = "none"
    text: str = ""
    text_color: str = "#D1D4DCFF"
    text_size: str = "small"
    text_halign: str = "center"
    text_valign: str = "center"
    tooltip: str = ""
    group: str = ""
    created_at: int = 0

    kind: str = field(default="box", init=False)


@dataclass
class Line:
    id: int
    x1: int
    y1: float
    x2: int
    y2: float
    color: str = "#2962FFFF"
    width: int = 1
    style: LineStyle = "solid"
    extend: Extend = "none"
    tooltip: str = ""
    group: str = ""
    created_at: int = 0

    kind: str = field(default="line", init=False)


@dataclass
class Label:
    id: int
    x: int
    y: float
    text: str = ""
    yloc: Literal["price", "abovebar", "belowbar"] = "price"
    style: str = "label_down"  # label_up/down/left/right/center, none, triangleup/down, arrowup/down, circle, ...
    color: str = "#2962FFFF"
    text_color: str = "#FFFFFFFF"
    size: str = "normal"
    tooltip: str = ""
    group: str = ""
    created_at: int = 0

    kind: str = field(default="label", init=False)


Drawing = Box | Line | Label


class DrawingStore:
    def __init__(self, max_boxes: int = 500, max_lines: int = 500, max_labels: int = 500):
        self.limits = {"box": max_boxes, "line": max_lines, "label": max_labels}
        self.live: dict[str, OrderedDict[int, Drawing]] = {k: OrderedDict() for k in self.limits}
        self.next_id = 1

    def _add(self, obj: Drawing) -> Drawing:
        bucket = self.live[obj.kind]
        bucket[obj.id] = obj
        while len(bucket) > self.limits[obj.kind]:
            bucket.popitem(last=False)  # Pine deletes the oldest object
        return obj

    def new_box(self, **kw) -> Box:
        obj = Box(id=self.next_id, **kw)
        self.next_id += 1
        return self._add(obj)  # type: ignore[return-value]

    def new_line(self, **kw) -> Line:
        obj = Line(id=self.next_id, **kw)
        self.next_id += 1
        return self._add(obj)  # type: ignore[return-value]

    def new_label(self, **kw) -> Label:
        obj = Label(id=self.next_id, **kw)
        self.next_id += 1
        return self._add(obj)  # type: ignore[return-value]

    def delete(self, obj: Drawing | None) -> None:
        if obj is not None:
            self.live[obj.kind].pop(obj.id, None)

    def all(self) -> list[Drawing]:
        return [o for k in ("box", "line", "label") for o in self.live[k].values()]

    def to_json(self) -> list[dict]:
        return [asdict(o) for o in self.all()]
