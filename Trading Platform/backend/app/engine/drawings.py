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
from dataclasses import dataclass, field
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
    tooltip_title: str = ""  # hover card heading (the script name when empty)
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
    tooltip_title: str = ""  # hover card heading (the script name when empty)
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
    tooltip_title: str = ""  # hover card heading (the script name when empty)
    group: str = ""
    created_at: int = 0

    kind: str = field(default="label", init=False)


Drawing = Box | Line | Label


def _copy_drawing(self, memo):
    """Every field is an immutable scalar, so a copy of the attribute dict is a full deep copy. Runner snapshots
    and last-bar views deep-copy thousands of drawings per step; the generic path rebuilt each field."""
    new = object.__new__(type(self))
    new.__dict__.update(self.__dict__)
    memo[id(self)] = new
    return new


for _cls in (Box, Line, Label):
    _cls.__deepcopy__ = _copy_drawing


class DrawingStore:
    def __init__(self, max_boxes: int = 500, max_lines: int = 500, max_labels: int = 500,
                 group_limits: dict[str, dict[str, int]] | None = None):
        self.limits = {"box": max_boxes, "line": max_lines, "label": max_labels}
        self.live: dict[str, OrderedDict[int, Drawing]] = {k: OrderedDict() for k in self.limits}
        # Groups with their own quota (``ScriptMeta.group_limits``): their objects never count towards, nor
        # push out, the script's other drawings, and are listed after them.
        self.group_limits = {g: dict(lim) for g, lim in (group_limits or {}).items()}
        self.group_live: dict[str, dict[str, OrderedDict[int, Drawing]]] = {
            g: {k: OrderedDict() for k in self.limits} for g in self.group_limits}
        self.next_id = 1
        self.next_group_id = 1_000_000_001  # own id range, so the other drawings keep their ids

    def _new_id(self, group: str) -> int:
        if group in self.group_live:
            self.next_group_id += 1
            return self.next_group_id - 1
        self.next_id += 1
        return self.next_id - 1

    def _bucket(self, obj: Drawing) -> tuple[OrderedDict, int]:
        own = self.group_live.get(obj.group)
        if own is not None:
            return own[obj.kind], self.group_limits[obj.group].get(obj.kind, self.limits[obj.kind])
        return self.live[obj.kind], self.limits[obj.kind]

    def _add(self, obj: Drawing) -> Drawing:
        bucket, limit = self._bucket(obj)
        bucket[obj.id] = obj
        while len(bucket) > limit:
            bucket.popitem(last=False)  # Pine deletes the oldest object
        return obj

    def new_box(self, **kw) -> Box:
        obj = Box(id=self._new_id(kw.get("group", "")), **kw)
        return self._add(obj)  # type: ignore[return-value]

    def new_line(self, **kw) -> Line:
        obj = Line(id=self._new_id(kw.get("group", "")), **kw)
        return self._add(obj)  # type: ignore[return-value]

    def new_label(self, **kw) -> Label:
        obj = Label(id=self._new_id(kw.get("group", "")), **kw)
        return self._add(obj)  # type: ignore[return-value]

    def delete(self, obj: Drawing | None) -> None:
        if obj is not None:
            self._bucket(obj)[0].pop(obj.id, None)

    def all(self) -> list[Drawing]:
        out = [o for k in ("box", "line", "label") for o in self.live[k].values()]
        for own in self.group_live.values():
            out.extend(o for k in ("box", "line", "label") for o in own[k].values())
        return out

    def to_json(self) -> list[dict]:
        # Same as dataclasses.asdict (fields are scalars), without its per-field recursion; attributes a script
        # attached itself (``_name``) are not fields and stay out, as with asdict.
        # ``kind`` is a class-level default (init=False), not in the instance dict.
        return [{**{k: v for k, v in o.__dict__.items() if k[0] != '_'}, 'kind': o.kind} for o in self.all()]
