"""Figures for the 3-preset denoise sensitivity report.

Every figure uses the **fixed shared scales** declared in
``experiment_contract.Tolerances`` so panels from different presets are
visually comparable. Nothing here chooses a colour limit from the data.

Matplotlib is used with the Agg backend; ``cartopy`` and ``cmocean`` are only
imported by the functions that need them so the rest of the report still
builds without the optional map dependencies.
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from pathlib import Path
from typing import Any

import matplotlib

matplotlib.use("Agg")

import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402
from experiment_contract import TOLERANCES  # noqa: E402

logger = logging.getLogger(__name__)

FIG_DPI = 150
SV_VMIN, SV_VMAX = TOLERANCES.sv_color_limits_db
DIFF_LIMIT = TOLERANCES.sv_diff_limit_db

#: Stable colour per preset across every multi-preset figure.
PRESET_COLORS = {
    "ryan-inspired": "#1b3b6f",
    "tpv1": "#c1663a",
    "tpv3": "#2e7d5b",
}
PRESET_ORDER = ("ryan-inspired", "tpv1", "tpv3")


def _flat_axes(ax) -> None:
    for spine in ("top", "right"):
        ax.spines[spine].set_visible(False)
    ax.grid(True, alpha=0.25, linewidth=0.6)


def _save(fig, path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(path, dpi=FIG_DPI, bbox_inches="tight", facecolor="white")
    plt.close(fig)
    logger.info("Wrote figure %s", path)
    return path


# ── Array reduction ────────────────────────────────────────────────────────


def block_reduce(
    arr: np.ndarray, max_rows: int, max_cols: int, how: str = "mean_linear"
) -> np.ndarray:
    """Downsample a 2-D array to at most ``max_rows × max_cols``.

    ``mean_linear`` converts dB → linear, averages, converts back, so the mean
    is energy-correct. ``any`` / ``majority`` operate on boolean/categorical
    inputs.
    """
    arr = np.asarray(arr)
    rows, cols = arr.shape
    fr = max(1, int(np.ceil(rows / max_rows)))
    fc = max(1, int(np.ceil(cols / max_cols)))
    if fr == 1 and fc == 1:
        return arr

    pad_r = (-rows) % fr
    pad_c = (-cols) % fc
    if how == "any":
        padded = np.pad(arr.astype(bool), ((0, pad_r), (0, pad_c)), constant_values=False)
        blocks = padded.reshape(padded.shape[0] // fr, fr, padded.shape[1] // fc, fc)
        return blocks.any(axis=(1, 3))
    if how == "majority":
        padded = np.pad(arr.astype(float), ((0, pad_r), (0, pad_c)), constant_values=np.nan)
        blocks = padded.reshape(padded.shape[0] // fr, fr, padded.shape[1] // fc, fc)
        flat = blocks.transpose(0, 2, 1, 3).reshape(blocks.shape[0], blocks.shape[2], -1)
        codes = np.unique(arr[np.isfinite(arr)]) if np.isfinite(arr).any() else np.array([])
        if codes.size == 0:
            return np.full(flat.shape[:2], np.nan)
        counts = np.stack([(flat == c).sum(axis=-1) for c in codes], axis=-1)
        winner = codes[np.argmax(counts, axis=-1)]
        return np.where(counts.max(axis=-1) > 0, winner, np.nan)

    padded = np.pad(arr.astype(float), ((0, pad_r), (0, pad_c)), constant_values=np.nan)
    blocks = padded.reshape(padded.shape[0] // fr, fr, padded.shape[1] // fc, fc)
    if how == "mean_linear":
        with np.errstate(over="ignore", invalid="ignore"):
            linear = np.power(10.0, blocks / 10.0)
            mean = np.nanmean(linear, axis=(1, 3))
            out = 10.0 * np.log10(np.where(mean > 0, mean, np.nan))
        return out
    if how == "mean":
        return np.nanmean(blocks, axis=(1, 3))
    raise ValueError(f"Unknown reduction {how!r}")


# ── Echograms ──────────────────────────────────────────────────────────────


def echogram_triptych(
    panels: Sequence[tuple[str, np.ndarray, np.ndarray, np.ndarray]],
    title: str,
    out_path: Path,
) -> Path:
    """Stacked Sv panels (source / denoised / pruned) on the fixed shared scale.

    Each panel is ``(label, sv_2d[time, depth], time_axis, depth_axis)``.
    """
    fig, axes = plt.subplots(
        len(panels), 1, figsize=(13, 3.2 * len(panels)), sharex=False
    )
    axes = np.atleast_1d(axes)
    mesh = None
    for ax, (label, sv, t_axis, d_axis) in zip(axes, panels):
        if sv.size == 0:
            ax.text(0.5, 0.5, f"{label}: no data", ha="center", va="center")
            ax.set_axis_off()
            continue
        mesh = ax.pcolormesh(
            t_axis, d_axis, sv.T, vmin=SV_VMIN, vmax=SV_VMAX,
            cmap="ocean_r", shading="auto", rasterized=True,
        )
        ax.invert_yaxis()
        ax.set_ylabel("Depth (m)")
        ax.set_title(label, loc="left", fontsize=11)
    if mesh is not None:
        fig.colorbar(mesh, ax=list(axes), label="Sv (dB re 1 m⁻¹)", pad=0.02)
    fig.suptitle(title, fontsize=13)
    return _save(fig, out_path)


def difference_echogram(
    delta: np.ndarray,
    t_axis: np.ndarray,
    d_axis: np.ndarray,
    title: str,
    out_path: Path,
) -> Path:
    """Candidate − baseline dB delta on a symmetric scale, finite overlap only."""
    fig, ax = plt.subplots(figsize=(13, 4))
    mesh = ax.pcolormesh(
        t_axis, d_axis, delta.T, vmin=-DIFF_LIMIT, vmax=DIFF_LIMIT,
        cmap="RdBu_r", shading="auto", rasterized=True,
    )
    ax.invert_yaxis()
    ax.set_ylabel("Depth (m)")
    ax.set_title(title, loc="left", fontsize=12)
    fig.colorbar(mesh, ax=ax, label="ΔSv vs baseline (dB)", pad=0.02)
    fig.text(
        0.01, -0.04,
        "White = no finite overlap (one or both arms masked the cell); "
        "categorical disagreement is shown separately.",
        fontsize=9,
    )
    return _save(fig, out_path)


#: Categorical codes for the mask-disagreement panel.
DISAGREEMENT_CODES = {
    "agree_unflagged": 0,
    "baseline_only": 1,
    "candidate_only": 2,
    "both": 3,
}
_DISAGREEMENT_COLORS = ["#f2f2f2", "#1b3b6f", "#c1663a", "#5a5a5a"]


def mask_disagreement_map(
    codes: np.ndarray,
    t_axis: np.ndarray,
    d_axis: np.ndarray,
    title: str,
    out_path: Path,
) -> Path:
    """Where two presets disagree about flagging — baseline-only / candidate-only / both."""
    from matplotlib.colors import BoundaryNorm, ListedColormap
    from matplotlib.patches import Patch

    cmap = ListedColormap(_DISAGREEMENT_COLORS)
    norm = BoundaryNorm([-0.5, 0.5, 1.5, 2.5, 3.5], cmap.N)

    fig, ax = plt.subplots(figsize=(13, 4))
    ax.pcolormesh(
        t_axis, d_axis, codes.T, cmap=cmap, norm=norm,
        shading="auto", rasterized=True,
    )
    ax.invert_yaxis()
    ax.set_ylabel("Depth (m)")
    ax.set_title(title, loc="left", fontsize=12)
    ax.legend(
        handles=[
            Patch(facecolor=_DISAGREEMENT_COLORS[0], label="neither flagged"),
            Patch(facecolor=_DISAGREEMENT_COLORS[1], label="baseline only"),
            Patch(facecolor=_DISAGREEMENT_COLORS[2], label="candidate only"),
            Patch(facecolor=_DISAGREEMENT_COLORS[3], label="both flagged"),
        ],
        loc="upper right", framealpha=0.9, fontsize=9,
    )
    return _save(fig, out_path)


# ── Mask fractions ─────────────────────────────────────────────────────────


def mask_fraction_small_multiples(
    stats: dict,
    title: str,
    out_path: Path,
) -> Path:
    """Per-filter flagged fraction per preset, with the union and the overlaps.

    Deliberately **not** a stacked bar chart: the masks overlap, so stacking
    would imply an additive decomposition that does not exist. Per-mask bars,
    the union, and the unique contributions are drawn side by side, and the
    pairwise overlaps are annotated underneath.

    *stats* maps ``preset → {filter_name: {"fraction": float|None,
    "unique": float|None}}`` plus a ``"__union__"`` and ``"__overlap__"`` key.
    """
    presets = [p for p in PRESET_ORDER if p in stats]
    filters = sorted(
        {f for p in presets for f in stats[p] if not f.startswith("__")}
    )
    labels = filters + ["union"]

    x = np.arange(len(labels))
    width = 0.8 / max(1, len(presets))

    fig, (ax, ax_txt) = plt.subplots(
        2, 1, figsize=(10, 6.2), gridspec_kw={"height_ratios": [3, 1]}
    )
    for i, preset in enumerate(presets):
        entry = stats[preset]
        totals = [
            (entry.get(f) or {}).get("fraction") for f in filters
        ] + [(entry.get("__union__") or {}).get("fraction")]
        uniques = [
            (entry.get(f) or {}).get("unique") for f in filters
        ] + [None]
        offset = (i - (len(presets) - 1) / 2) * width
        totals_pct = [np.nan if v is None else v * 100 for v in totals]
        uniques_pct = [np.nan if v is None else v * 100 for v in uniques]
        ax.bar(
            x + offset, totals_pct, width * 0.95,
            color=PRESET_COLORS.get(preset, "#666"), label=preset, alpha=0.55,
        )
        ax.bar(
            x + offset, uniques_pct, width * 0.55,
            color=PRESET_COLORS.get(preset, "#666"),
            edgecolor="white", linewidth=0.6,
        )

    ax.set_xticks(x)
    ax.set_xticklabels(labels)
    ax.set_ylabel("% of source-finite cells in band")
    ax.set_title(title, loc="left", fontsize=12)
    ax.legend(fontsize=9, title="preset (pale = total, solid = unique)", title_fontsize=9)
    _flat_axes(ax)

    ax_txt.set_axis_off()
    lines = ["Pairwise overlaps (% of source-finite cells in band):"]
    for preset in presets:
        overlaps = stats[preset].get("__overlap__") or {}
        rendered = ", ".join(
            f"{k}={'—' if v is None else f'{v * 100:.3f}%'}"
            for k, v in sorted(overlaps.items())
        )
        lines.append(f"  {preset}: {rendered or 'none'}")
    ax_txt.text(0, 1, "\n".join(lines), va="top", fontsize=9, family="monospace")
    return _save(fig, out_path)


# ── Distributions and profiles ─────────────────────────────────────────────


def sv_histogram_overlay(
    series: dict[str, dict[str, np.ndarray]],
    title: str,
    out_path: Path,
) -> Path:
    """Sv distribution before vs after denoising, overlaid across presets.

    *series* maps ``preset → {"source": values, "denoised": values}``. The
    source arm is identical for all presets (shared immutable input) so it is
    drawn once as a filled reference.
    """
    bins = np.linspace(SV_VMIN - 20, SV_VMAX + 10, 140)
    fig, ax = plt.subplots(figsize=(10, 4.6))

    drew_source = False
    for preset in PRESET_ORDER:
        entry = series.get(preset)
        if not entry:
            continue
        if not drew_source and entry.get("source") is not None:
            src = entry["source"]
            src = src[np.isfinite(src)]
            if src.size:
                ax.hist(
                    src, bins=bins, density=True, color="#bbbbbb",
                    label="source Sv (pre-denoise)", alpha=0.8,
                )
                drew_source = True
        vals = entry.get("denoised")
        if vals is None:
            continue
        vals = vals[np.isfinite(vals)]
        if vals.size:
            ax.hist(
                vals, bins=bins, density=True, histtype="step", linewidth=1.6,
                color=PRESET_COLORS.get(preset, "#666"), label=preset,
            )

    ax.set_xlabel("Sv (dB re 1 m⁻¹)")
    ax.set_ylabel("Density")
    ax.set_title(title, loc="left", fontsize=12)
    ax.legend(fontsize=9)
    _flat_axes(ax)
    return _save(fig, out_path)


def mvbs_depth_profiles(
    profiles: dict[str, tuple[np.ndarray, np.ndarray]],
    title: str,
    out_path: Path,
    band: tuple[float, float] | None = None,
) -> Path:
    """Mean MVBS depth profile per preset (linear-space mean, plotted in dB)."""
    fig, ax = plt.subplots(figsize=(5.6, 7.2))
    for preset in PRESET_ORDER:
        if preset not in profiles:
            continue
        depth, sv_db = profiles[preset]
        ax.plot(
            sv_db, depth, linewidth=1.5,
            color=PRESET_COLORS.get(preset, "#666"), label=preset,
        )
    if band:
        ax.axhspan(band[0], band[1], color="#f0f4f8", zorder=0,
                   label=f"integration band {band[0]:.0f}–{band[1]:.0f} m")
    ax.invert_yaxis()
    ax.set_xlabel("Mean MVBS (dB re 1 m⁻¹)")
    ax.set_ylabel("Depth (m)")
    ax.set_title(title, loc="left", fontsize=12)
    ax.legend(fontsize=9)
    _flat_axes(ax)
    return _save(fig, out_path)


def integrated_nasc_panels(
    native: dict[str, tuple[np.ndarray, np.ndarray]],
    common: dict[str, tuple[np.ndarray, np.ndarray]],
    title: str,
    out_path: Path,
    alignment_note: str = "",
) -> Path:
    """Depth-integrated NASC along track — native support and common support."""
    fig, axes = plt.subplots(2, 1, figsize=(11, 7), sharex=True)
    for ax, data, label in (
        (axes[0], native, "Native support (each preset's own retained bins)"),
        (axes[1], common, "Common support (bins finite in all three presets)"),
    ):
        for preset in PRESET_ORDER:
            if preset not in data:
                continue
            distance, values = data[preset]
            ax.plot(
                distance, values, linewidth=1.4, marker="o", markersize=3,
                color=PRESET_COLORS.get(preset, "#666"), label=preset,
            )
        ax.set_ylabel("NASC (m² nmi⁻²)")
        ax.set_title(label, loc="left", fontsize=11)
        ax.set_yscale("symlog", linthresh=1.0)
        _flat_axes(ax)
    axes[1].set_xlabel("Along-track distance (nmi)")
    axes[0].legend(fontsize=9)
    if alignment_note:
        fig.text(0.01, -0.02, alignment_note, fontsize=9)
    fig.suptitle(title, fontsize=13)
    return _save(fig, out_path)


# ── Map ────────────────────────────────────────────────────────────────────


def nasc_track_map(
    tracks: dict[str, dict[str, np.ndarray]],
    title: str,
    out_path: Path,
    natural_earth_dir: Path | None = None,
) -> Path | None:
    """Track coloured by depth-integrated NASC, with a Pacific inset locator.

    Uses the repaired per-distance-bin NASC coordinates. Returns ``None`` when
    cartopy is unavailable so the rest of the report still builds.
    """
    try:
        import cartopy.crs as ccrs
        import cartopy.feature as cfeature
    except ImportError:
        logger.warning("cartopy not installed — skipping the NASC track map")
        return None

    if natural_earth_dir:
        import cartopy

        cartopy.config["pre_existing_data_dir"] = str(natural_earth_dir)
        cartopy.config["data_dir"] = str(natural_earth_dir)

    try:
        import cmocean

        cmap = cmocean.cm.thermal
    except ImportError:
        cmap = plt.get_cmap("viridis")

    lats = np.concatenate([t["latitude"] for t in tracks.values()]) if tracks else np.array([])
    lons = np.concatenate([t["longitude"] for t in tracks.values()]) if tracks else np.array([])
    finite = np.isfinite(lats) & np.isfinite(lons)
    if finite.sum() == 0:
        logger.warning("No finite NASC positions — skipping the track map")
        return None
    lat_c, lon_c = float(np.mean(lats[finite])), float(np.mean(lons[finite]))
    pad = max(0.05, float(np.ptp(lats[finite])), float(np.ptp(lons[finite]))) * 0.8

    proj = ccrs.PlateCarree()
    fig, axes = plt.subplots(
        1, len(tracks), figsize=(5.2 * max(1, len(tracks)), 5.4),
        subplot_kw={"projection": proj},
    )
    axes = np.atleast_1d(axes)

    all_values = np.concatenate([t["nasc"] for t in tracks.values()])
    positive = all_values[np.isfinite(all_values) & (all_values > 0)]
    vmin = float(np.percentile(positive, 5)) if positive.size else 0.0
    vmax = float(np.percentile(positive, 95)) if positive.size else 1.0

    scatter = None
    for ax, (preset, track) in zip(axes, tracks.items()):
        ax.set_extent(
            [lon_c - pad, lon_c + pad, lat_c - pad, lat_c + pad], crs=proj
        )
        ax.add_feature(cfeature.OCEAN, facecolor="#eaf1f7")
        ax.add_feature(cfeature.LAND, facecolor="#e8e4dc")
        ax.add_feature(cfeature.COASTLINE, linewidth=0.5)
        gl = ax.gridlines(draw_labels=True, linewidth=0.4, alpha=0.4)
        gl.top_labels = gl.right_labels = False
        scatter = ax.scatter(
            track["longitude"], track["latitude"], c=track["nasc"],
            cmap=cmap, vmin=vmin, vmax=vmax, s=42, edgecolor="k",
            linewidth=0.3, transform=proj, zorder=5,
        )
        ax.set_title(preset, fontsize=11)

    if scatter is not None:
        fig.colorbar(
            scatter, ax=list(axes), orientation="horizontal",
            label="Depth-integrated NASC (m² nmi⁻²)", pad=0.08, shrink=0.7,
        )

    # Pacific inset locator, tucked into the bottom-left margin so it never
    # overlaps the first panel.
    inset = fig.add_axes([0.005, 0.02, 0.13, 0.24], projection=proj)
    inset.set_extent([-180, -100, -30, 30], crs=proj)
    inset.add_feature(cfeature.OCEAN, facecolor="#eaf1f7")
    inset.add_feature(cfeature.LAND, facecolor="#e8e4dc")
    inset.add_feature(cfeature.COASTLINE, linewidth=0.3)
    inset.plot(lon_c, lat_c, marker="*", color="#c1663a", markersize=11, transform=proj)
    inset.set_title("Tropical Pacific", fontsize=8)

    fig.suptitle(title, fontsize=13)
    return _save(fig, out_path)


def natural_earth_versions(natural_earth_dir: Path | None) -> dict[str, Any]:
    """Record which Natural Earth assets the map used, for reproducibility."""
    info: dict[str, Any] = {"cache_dir": str(natural_earth_dir) if natural_earth_dir else None}
    try:
        import cartopy

        info["cartopy_version"] = cartopy.__version__
    except ImportError:
        info["cartopy_version"] = None
    if natural_earth_dir and Path(natural_earth_dir).exists():
        info["files"] = sorted(
            p.name for p in Path(natural_earth_dir).rglob("*") if p.is_file()
        )
    return info
