"""GPS/navigation processing, loaded when a geotrack operation is requested."""
from __future__ import annotations

from importlib import import_module
from typing import Any

__all__ = ["convert", "generate_tiles", "process", "generate_report"]


def __getattr__(name: str) -> Any:
    if name not in __all__:
        raise AttributeError(name)
    module = "report" if name == "generate_report" else "processor"
    value = getattr(import_module(f"{__name__}.{module}"), name)
    globals()[name] = value
    return value
