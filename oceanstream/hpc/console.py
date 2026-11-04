"""Progress messages on stderr, so stdout stays clean for machine-readable output."""

from __future__ import annotations

import sys


class HpcError(RuntimeError):
    """A step that cannot go on; the message says what to do about it."""


def _colour(code: str) -> tuple[str, str]:
    if sys.stderr.isatty():
        return f"\033[{code}m", "\033[0m"
    return "", ""


def log(message: str) -> None:
    on, off = _colour("1;34")
    print(f"{on}==>{off} {message}", file=sys.stderr, flush=True)


def ok(message: str) -> None:
    on, off = _colour("1;32")
    print(f"{on}  ✓{off} {message}", file=sys.stderr, flush=True)


def warn(message: str) -> None:
    on, off = _colour("1;33")
    print(f"{on}  !{off} {message}", file=sys.stderr, flush=True)


def info(message: str) -> None:
    on, off = _colour("2")
    print(f"{on}    {message}{off}", file=sys.stderr, flush=True)
