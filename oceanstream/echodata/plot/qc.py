"""QC flag loading and echogram overlay rendering.

Supports a structured JSON schema for known-bad data windows (e.g. weather
events, cross-talk bursts, hardware issues) that should be visually marked
on echograms *without* being silently masked out of the underlying data.

This is the "flag, don't drop" convention recommended by ICES CRR 259 —
downstream consumers (biomass calculations, AI analysis, dashboards) can
read the same JSON and decide whether to honor the flags.

QC JSON schema
--------------
{
  "campaign_id": "tpos_saildrone_2023",
  "flagged_windows": [
    {
      "date": "2023-10-09",
      "start_utc": "2023-10-09T08:00:00Z",
      "end_utc":   "2023-10-09T09:00:00Z",
      "categories": ["long_pulse"],
      "channels":   ["*"],
      "type":       "attenuation_event",
      "cause":      "bubble_sweepdown_high_seas",
      "action":     "exclude_from_biomass",
      "notes":      "Reduced Sv across water column..."
    }
  ]
}

Fields
------
date          : YYYY-MM-DD — day this window applies to (optional; if omitted,
                inferred from start_utc)
start_utc,    : ISO 8601 UTC timestamps bounding the window
end_utc
categories    : list of pulse categories to apply to (e.g. "short_pulse",
                "long_pulse"), or ["*"] for all
channels      : list of channel labels to match (substring), or ["*"] for all
type          : short tag (e.g. "attenuation_event", "cross_talk",
                "hardware_dropout"); rendered as label
cause         : free-text underlying cause; not rendered but useful for reports
action        : consumer hint (e.g. "exclude_from_biomass"); not rendered
notes         : longer description; not rendered on the plot itself
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Optional, Union

if TYPE_CHECKING:
    import matplotlib.axes
    import pandas as pd

logger = logging.getLogger(__name__)


@dataclass
class QCWindow:
    """A single flagged time window."""

    start_utc: "pd.Timestamp"
    end_utc: "pd.Timestamp"
    type: str = "flagged"
    cause: str = ""
    action: str = ""
    notes: str = ""
    date: Optional[str] = None
    categories: list[str] = field(default_factory=lambda: ["*"])
    channels: list[str] = field(default_factory=lambda: ["*"])

    def applies_to(self, category: Optional[str], channel_label: Optional[str]) -> bool:
        """Check whether this window applies to a given category and channel."""
        if category is not None and "*" not in self.categories:
            if category not in self.categories:
                return False
        if channel_label is not None and "*" not in self.channels:
            # Match by substring (e.g. "38-kHz" matches "EKA-...-ES38-...-38-kHz")
            if not any(ch.lower() in channel_label.lower() for ch in self.channels):
                return False
        return True


def load_qc_windows(qc_file: Union[str, Path]) -> list[QCWindow]:
    """Load QC windows from a JSON file.

    Parameters
    ----------
    qc_file : Path
        Path to the QC JSON file.

    Returns
    -------
    list[QCWindow]
        Parsed windows. Returns empty list if file is missing or malformed
        (logs a warning).
    """
    import pandas as pd

    qc_path = Path(qc_file)
    if not qc_path.exists():
        logger.warning("QC file not found: %s", qc_path)
        return []

    try:
        data = json.loads(qc_path.read_text())
    except json.JSONDecodeError as exc:
        logger.warning("QC file %s is not valid JSON: %s", qc_path, exc)
        return []

    windows: list[QCWindow] = []
    for raw in data.get("flagged_windows", []):
        try:
            start = pd.to_datetime(raw["start_utc"], utc=True).tz_convert(None)
            end = pd.to_datetime(raw["end_utc"], utc=True).tz_convert(None)
        except (KeyError, ValueError) as exc:
            logger.warning("Skipping malformed QC window %s: %s", raw, exc)
            continue

        windows.append(
            QCWindow(
                start_utc=start,
                end_utc=end,
                type=raw.get("type", "flagged"),
                cause=raw.get("cause", ""),
                action=raw.get("action", ""),
                notes=raw.get("notes", ""),
                date=raw.get("date"),
                categories=list(raw.get("categories", ["*"])),
                channels=list(raw.get("channels", ["*"])),
            )
        )

    logger.info("Loaded %d QC window(s) from %s", len(windows), qc_path)
    return windows


def filter_qc_windows(
    windows: list[QCWindow],
    *,
    date: Optional[str] = None,
    category: Optional[str] = None,
    channel_label: Optional[str] = None,
) -> list[QCWindow]:
    """Filter windows by date, category, and/or channel label."""
    out: list[QCWindow] = []
    for w in windows:
        if date is not None and w.date is not None and w.date != date:
            continue
        if not w.applies_to(category, channel_label):
            continue
        out.append(w)
    return out


# Color scheme by flag type (matplotlib-friendly). Exposed publicly so
# alternative renderers (e.g. combined per-day echograms with non-datetime
# x-axes) can reuse the same palette.
TYPE_COLORS = {
    "attenuation_event": "#B85450",       # muted red
    "cross_talk":         "#8B6F47",       # brown
    "hardware_dropout":   "#4A6FA5",       # muted blue
    "weather":            "#5B7F5B",       # muted green
    "flagged":            "#666666",       # grey (default)
}
_TYPE_COLORS = TYPE_COLORS  # backward-compat alias


def draw_qc_overlay(
    ax: "matplotlib.axes.Axes",
    windows: list[QCWindow],
    *,
    ping_time_min: Optional["pd.Timestamp"] = None,
    ping_time_max: Optional["pd.Timestamp"] = None,
    label_top: bool = True,
) -> int:
    """Draw QC windows as translucent bands on an echogram.

    Each window becomes:
      - a shaded axvspan across the flagged time range
      - dashed vertical lines at the start and end
      - a caption at the top of the axes with the flag type

    Parameters
    ----------
    ax : matplotlib.axes.Axes
        Axes to draw on (must have a datetime x-axis).
    windows : list[QCWindow]
        Windows to draw (assumed already filtered by category/channel/date).
    ping_time_min, ping_time_max : pd.Timestamp, optional
        Time range of the echogram. Windows fully outside this range are
        skipped; windows partially outside are clipped.
    label_top : bool
        Whether to write the flag type + cause as a caption at the top.

    Returns
    -------
    int
        Number of windows actually drawn (excludes those out of range).
    """
    if not windows:
        return 0

    drawn = 0
    for w in windows:
        # Skip windows entirely outside the plot range
        if ping_time_min is not None and w.end_utc < ping_time_min:
            continue
        if ping_time_max is not None and w.start_utc > ping_time_max:
            continue

        # Clip to visible range for accurate label placement
        start = w.start_utc
        end = w.end_utc
        if ping_time_min is not None:
            start = max(start, ping_time_min)
        if ping_time_max is not None:
            end = min(end, ping_time_max)

        color = _TYPE_COLORS.get(w.type, _TYPE_COLORS["flagged"])

        # Shaded band
        ax.axvspan(
            start, end,
            facecolor=color, alpha=0.18, zorder=4, edgecolor="none",
        )

        # Dashed boundary lines
        ax.axvline(w.start_utc, color=color, lw=1.4, ls="--", alpha=0.85, zorder=5)
        ax.axvline(w.end_utc, color=color, lw=1.4, ls="--", alpha=0.85, zorder=5)

        # Caption at top
        if label_top:
            label = w.type.replace("_", " ")
            if w.cause:
                label = f"{label} — {w.cause.replace('_', ' ')}"
            mid = start + (end - start) / 2
            ax.annotate(
                label,
                xy=(mid, 1.0), xycoords=("data", "axes fraction"),
                xytext=(0, 4), textcoords="offset points",
                ha="center", va="bottom",
                fontsize=11, fontweight="bold", color=color,
                bbox=dict(
                    boxstyle="round,pad=0.35",
                    facecolor="white", edgecolor=color,
                    linewidth=1.2, alpha=0.92,
                ),
                zorder=6,
            )

        drawn += 1

    return drawn
