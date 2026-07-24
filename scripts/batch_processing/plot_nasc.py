"""
Standalone NASC visualization — depth-resolved echograms + transect + map.

Usage:
    python plot_nasc.py [--zarr-dir local-raw-01] [--day 2023-06-25] [--category long_pulse]

Produces 3 panels:
  1. Echogram heatmap: distance (x) × depth (y), colored by NASC_log
  2. Depth-integrated NASC along transect (1D bar)
  3. Geographic track colored by integrated NASC
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import cmocean  # noqa: F401 — registers colormaps
import matplotlib.pyplot as plt
import numpy as np
import xarray as xr


def load_nasc(zarr_dir: str, day: str, category: str) -> xr.Dataset:
    path = Path(zarr_dir) / day / f"{day}--{category}--nasc.zarr"
    if not path.exists():
        sys.exit(f"NASC zarr not found: {path}")
    return xr.open_zarr(path)


def summarize(ds: xr.Dataset, channel_idx: int = 0,
              surface_exclusion_depth: float = 10.0) -> None:
    """Print key statistics for sanity-checking."""
    nasc_full = ds["NASC"].isel(channel=channel_idx).values
    depth = ds.depth.values
    ch_name = str(ds.channel.values[channel_idx])

    # Apply surface exclusion
    clean_mask = depth >= surface_exclusion_depth
    nasc = nasc_full[:, clean_mask]
    nasc_log = ds["NASC_log"].isel(channel=channel_idx).values[:, clean_mask]
    finite = nasc[np.isfinite(nasc)]

    print(f"Channel: {ch_name}")
    print(f"  Surface exclusion: {surface_exclusion_depth:.0f} m (platform-dependent)")
    print(f"  Grid: {ds.sizes['distance']} distance × {clean_mask.sum()} depth bins "
          f"({surface_exclusion_depth:.0f}–{float(depth[-1]):.0f} m)")
    print(f"  Distance: {float(ds.distance[0]):.1f} – {float(ds.distance[-1]):.1f} nmi")
    print(f"  NASC (linear): min={finite.min():.2f}, max={finite.max():.2f}, "
          f"median={np.median(finite):.2f}, mean={finite.mean():.2f}")
    print(f"  NASC_log (dB): min={nasc_log[np.isfinite(nasc_log)].min():.1f}, "
          f"max={nasc_log[np.isfinite(nasc_log)].max():.1f}, "
          f"mean={nasc_log[np.isfinite(nasc_log)].mean():.1f}")
    print(f"  Lat: {float(ds.latitude.min()):.4f} – {float(ds.latitude.max()):.4f}")
    print(f"  Lon: {float(ds.longitude.min()):.4f} – {float(ds.longitude.max()):.4f}")

    # Depth distribution of energy
    depth_clean = depth[clean_mask]
    depth_profile = np.nanmean(nasc, axis=0)
    top_depth = depth_clean[np.argmax(depth_profile)]
    print(f"  Peak NASC depth bin: {top_depth:.0f} m")

    # Depth-integrated NASC per distance bin (true sA)
    integrated = np.nansum(nasc, axis=1)
    print(f"  Depth-integrated sA: min={integrated.min():.0f}, max={integrated.max():.0f}, "
          f"mean={integrated.mean():.0f} m²/nmi²")

    # Surface bin warning
    if surface_exclusion_depth > 0:
        surf_nasc = nasc_full[:, depth < surface_exclusion_depth]
        if surf_nasc.size > 0:
            surf_mean = np.nanmean(surf_nasc)
            clean_mean = np.nanmean(nasc)
            ratio = surf_mean / max(clean_mean, 1e-10)
            print(f"  Excluded surface ({depth[0]:.0f}–{surface_exclusion_depth:.0f} m): "
                  f"mean={surf_mean:.0f} ({ratio:.0f}× clean mean)")
    print()


def plot_nasc_echogram(ds: xr.Dataset, channel_idx: int = 0, max_depth: float = 500.0,
                       min_depth: float = 10.0,
                       vmin: float = -5, vmax: float = 30, out_prefix: str = "nasc") -> str:
    """Create 4-panel NASC figure. Returns output file path.

    Excludes the near-surface bin (0 m) which is contaminated by transmit
    pulse ring-down / surface bubble noise.
    """
    nasc_log = ds["NASC_log"].isel(channel=channel_idx).values  # (distance, depth)
    nasc_lin = ds["NASC"].isel(channel=channel_idx).values
    dist = ds.distance.values
    depth = ds.depth.values
    lat = ds.latitude.values
    lon = ds.longitude.values
    ch_name = str(ds.channel.values[channel_idx])

    # Exclude near-surface noise + limit max depth
    depth_mask = (depth >= min_depth) & (depth <= max_depth)
    depth_plot = depth[depth_mask]
    nasc_log_plot = nasc_log[:, depth_mask]
    nasc_lin_plot = nasc_lin[:, depth_mask]

    fig, axes = plt.subplots(4, 1, figsize=(14, 14),
                             gridspec_kw={"height_ratios": [3, 0.8, 1.2, 1.2]})
    fig.suptitle(f"NASC — {ch_name}\n{out_prefix}  (surface bin excluded, {min_depth:.0f}–{max_depth:.0f} m)",
                 fontsize=13, y=0.98)

    # ── Panel 1: Echogram heatmap ─────────────────────────────────────
    ax1 = axes[0]
    im = ax1.pcolormesh(
        dist, depth_plot, nasc_log_plot.T,
        shading="auto", cmap="cmo.haline", vmin=vmin, vmax=vmax,
    )
    ax1.invert_yaxis()
    ax1.set_xlabel("Distance (nmi)")
    ax1.set_ylabel("Depth (m)")
    ax1.set_title("NASC (10·log₁₀) — depth-resolved echogram")
    cb = fig.colorbar(im, ax=ax1, pad=0.01, aspect=30)
    cb.set_label("NASC_log (dB re 1 m²/nmi²)")

    # ── Panel 2: Depth-integrated NASC (1D transect) ──────────────────
    ax2 = axes[1]
    integrated = np.nansum(nasc_lin_plot, axis=1)
    bar_width = np.diff(dist).mean() * 0.9 if len(dist) > 1 else 0.5
    ax2.bar(dist, integrated, width=bar_width, color="steelblue", edgecolor="none", alpha=0.8)
    ax2.set_xlabel("Distance (nmi)")
    ax2.set_ylabel("sA (m²/nmi²)")
    ax2.set_title(f"Depth-integrated NASC ({min_depth:.0f}–{max_depth:.0f} m)")
    ax2.set_xlim(dist[0] - bar_width, dist[-1] + bar_width)

    # ── Panel 3: Mean depth profile ───────────────────────────────────
    ax3 = axes[2]
    mean_profile = np.nanmean(nasc_lin_plot, axis=0)  # mean across all distance bins
    ax3.barh(depth_plot, mean_profile, height=np.diff(depth_plot[:2])[0] if len(depth_plot) > 1 else 10,
             color="darkorange", edgecolor="none", alpha=0.8)
    ax3.invert_yaxis()
    ax3.set_xlabel("Mean NASC (linear)")
    ax3.set_ylabel("Depth (m)")
    ax3.set_title("Depth profile (mean across transect)")

    # ── Panel 4: Geographic track colored by integrated NASC ──────────
    ax4 = axes[3]
    sc = ax4.scatter(lon, lat, c=integrated, cmap="plasma", s=20, edgecolors="none")
    ax4.set_xlabel("Longitude (°E)")
    ax4.set_ylabel("Latitude (°N)")
    ax4.set_title("Track colored by depth-integrated NASC")
    cb4 = fig.colorbar(sc, ax=ax4, pad=0.01, aspect=20)
    cb4.set_label("sA (m²/nmi²)")

    plt.tight_layout(rect=[0, 0, 1, 0.96])
    out_path = f"{out_prefix}_nasc_plot.png"
    fig.savefig(out_path, dpi=150, bbox_inches="tight")
    plt.close(fig)
    print(f"Saved: {out_path}")
    return out_path


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--zarr-dir", default="local-raw-01", help="Root output directory")
    parser.add_argument("--day", default="2023-06-25")
    parser.add_argument("--category", default="long_pulse")
    parser.add_argument("--surface-exclusion-depth", type=float, default=10.0,
                        help="Exclude depth bins above this (m). Platform-dependent: "
                             "Saildrone=10, vessel=5, mooring=0")
    parser.add_argument("--max-depth", type=float, default=500.0, help="Max depth to display (m)")
    parser.add_argument("--vmin", type=float, default=-5, help="NASC_log color min")
    parser.add_argument("--vmax", type=float, default=30, help="NASC_log color max")
    parser.add_argument("--channel", type=int, default=0, help="Channel index")
    args = parser.parse_args()

    ds = load_nasc(args.zarr_dir, args.day, args.category)
    prefix = f"{args.day}--{args.category}"

    summarize(ds, channel_idx=args.channel,
              surface_exclusion_depth=args.surface_exclusion_depth)
    plot_nasc_echogram(
        ds, channel_idx=args.channel, max_depth=args.max_depth,
        min_depth=args.surface_exclusion_depth,
        vmin=args.vmin, vmax=args.vmax, out_prefix=prefix,
    )

    # Also plot short_pulse if available and category is long_pulse
    if args.category == "long_pulse":
        sp_path = Path(args.zarr_dir) / args.day / f"{args.day}--short_pulse--nasc.zarr"
        if sp_path.exists():
            ds_sp = xr.open_zarr(sp_path)
            sp_prefix = f"{args.day}--short_pulse"
            print("--- short_pulse ---")
            summarize(ds_sp, channel_idx=0,
                      surface_exclusion_depth=args.surface_exclusion_depth)
            plot_nasc_echogram(
                ds_sp, channel_idx=0, max_depth=args.max_depth,
                min_depth=args.surface_exclusion_depth,
                vmin=args.vmin, vmax=args.vmax, out_prefix=sp_prefix,
            )
            ds_sp.close()

    ds.close()


if __name__ == "__main__":
    main()
