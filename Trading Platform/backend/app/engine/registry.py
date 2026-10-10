"""Registry of available scripts (indicators that may also emit strategy signals)."""
from __future__ import annotations

from .script import Script

_SCRIPTS: dict[str, type[Script]] = {}


def register(cls: type[Script]) -> type[Script]:
    sid = cls.meta.id
    if sid in _SCRIPTS and _SCRIPTS[sid] is not cls:
        raise ValueError(f"duplicate script id {sid!r}")
    _SCRIPTS[sid] = cls
    return cls


def _load_builtin() -> None:
    if _SCRIPTS:
        return
    from ..scripts.smc_master import SmcMaster

    register(SmcMaster)


def get_script(script_id: str) -> type[Script]:
    _load_builtin()
    try:
        return _SCRIPTS[script_id]
    except KeyError as exc:
        raise KeyError(f"unknown script {script_id!r}") from exc


def list_scripts() -> list[type[Script]]:
    _load_builtin()
    return list(_SCRIPTS.values())
