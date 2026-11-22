"""ADCP processing tools — Acoustic Doppler Current Profiler data."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Optional

from ..server import mcp_app

mcp = mcp_app

_ADCP_MISSING_MSG = (
    "ADCP dependencies not installed. "
    "Install with: pip install oceanstream[adcp]"
)


def _check_adcp_available() -> Optional[str]:
    """Return error message if ADCP deps are not available, else None."""
    try:
        # dolfyn needs the numpy 2 / scipy 1.14 shims before it is imported
        import oceanstream.adcp._compat  # noqa: F401
        import dolfyn  # noqa: F401
        return None
    except ImportError:
        return _ADCP_MISSING_MSG


@mcp.tool()
def adcp_process(
    input_source: str,
    output_dir: str = "./output",
    instrument_type: Optional[str] = None,
) -> dict[str, Any]:
    """Process ADCP data files (RDI .raw or Nortek .ad2cp).

    Reads raw ADCP binary files, applies beam-to-earth coordinate transforms,
    performs time averaging, and outputs processed velocity profiles.

    For RDI instruments: reads .raw/.000 binary files via dolfyn.
    For Nortek AD2CP: reads .ad2cp binary files, extracts echosounder amplitude.

    Args:
        input_source: Path to ADCP data file or directory
        output_dir: Output directory for processed data (default: ./output/)
        instrument_type: Instrument type hint ('rdi' or 'ad2cp'). Auto-detected from file extension if not specified.
    """
    err = _check_adcp_available()
    if err:
        return {"error": err}

    input_path = Path(input_source)
    if not input_path.exists():
        return {"error": f"Input path not found: {input_source}"}

    try:
        from oceanstream.adcp import process

        result = process(
            input_source=input_path,
            output_dir=Path(output_dir),
            instrument_type=instrument_type,
        )
        return {"status": "success", "result": str(result) if result else "completed"}
    except Exception as e:
        return {"error": f"ADCP processing failed: {e}"}
