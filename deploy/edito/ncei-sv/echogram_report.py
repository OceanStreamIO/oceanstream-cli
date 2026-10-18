#!/usr/bin/env python3
"""Echograms for comparing denoise runs on one survey day, for review by eye.

For each channel, renders the stored Sv and each run's denoised Sv on that channel's own
pings, so a multiplexed echosounder's empty pings take no width. Recording gaps are
closed up and marked with a vertical line. A run with a seabed line has it drawn on the
stored Sv and on its own panel. For runs whose transient detector flagged anything, a
second panel shows where. From one run's MVBS, it also renders frequency-difference
echograms (dB difference between two channels on a shared grid) and three-way splits of
them, with oceanstream's ``db_difference``.

Colours: Sv in viridis, removed or empty cells white, flags black on white, differences
in purple-orange; every panel is labelled in text, so nothing depends on colour alone.

Writes WebP images and an ``index.json`` describing them into ``--out``. Credentials,
endpoint and region come from the environment, as for ncei_fetch.py.
"""

# ruff: noqa: PLC0415, PLR0913, PLR0915, PLR0917 - imports from the job image; a plotting script
from __future__ import annotations

import argparse
import json
import os
import sys
import warnings
from pathlib import Path

TRANSIENT = 4
SV_RANGE = (-90.0, -50.0)
DIFF_RANGE = 12.0
#: Display grid: at most this many columns and rows per echogram.
MAX_COLS, MAX_ROWS = 2000, 500
#: Consecutive pings further apart than this start a new recording session.
GAP_MINUTES = 10.0
#: Frequency-difference cells: MVBS bins averaged per cell, in time and depth.
DIFF_CELL = (5, 5)
#: |difference| below this is "flat" in the three-way split, in dB.
FLAT_DB = 2.0
DIFF_PAIRS = (
    ("120", "38", 500.0, "rises with frequency, typical of fluid-like zooplankton",
     "falls with frequency, typical of swimbladdered fish"),
    ("18", "38", 1500.0, "stronger at 18 kHz, typical of resonant gas inclusions",
     "weaker at 18 kHz"),
)


def _khz(ds, i) -> str:
    return f"{float(ds['frequency_nominal'].values[i]) / 1e3:g}"


def _bin(values, fp: int, fr: int, how: str = "sv"):
    """Mean over fp x fr blocks, of linear Sv for 'sv' or of 0/1 for 'share'."""
    import numpy as np

    n_p, n_r = values.shape
    pad_p, pad_r = -n_p % fp, -n_r % fr
    data = np.pad(values.astype("float64"), ((0, pad_p), (0, pad_r)), constant_values=np.nan)
    if how == "sv":
        data = 10 ** (data / 10)
    blocks = data.reshape(data.shape[0] // fp, fp, data.shape[1] // fr, fr)
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", RuntimeWarning)
        out = np.nanmean(blocks, axis=(1, 3))
    return 10 * np.log10(out) if how == "sv" else out


def _time_axis(ax, times, fp: int, n_cols: int, label_size: int) -> None:
    """HH:MM ticks on a column axis, and lines where a recording gap was closed up."""
    import numpy as np

    times = np.asarray(times, dtype="datetime64[s]")
    ticks = np.linspace(0, n_cols - 1, 8).astype(int)
    ax.set_xticks(ticks)
    ax.set_xticklabels([str(times[min(t * fp, times.size - 1)])[11:16] for t in ticks],
                       fontsize=label_size)
    gaps = np.flatnonzero(np.diff(times) > np.timedelta64(int(GAP_MINUTES * 60), "s")) + 1
    for g in gaps:
        ax.axvline(g / fp - 0.5, color="black", lw=3)
        ax.axvline(g / fp - 0.5, color="white", lw=1.2, ls=(0, (3, 3)))
    ax.set_xlabel(f"Time, UTC (recording only; {len(gaps)} dashed lines mark gaps"
                  f" over {GAP_MINUTES:g} min)", fontsize=label_size)


def _save(fig, path: Path) -> None:
    import matplotlib.pyplot as plt

    fig.savefig(path, format="webp", dpi=100, pil_kwargs={"quality": 82, "method": 6})
    plt.close(fig)


def _echogram(path, data, depth_max, times, fp, title, seabed=None, flags=False):
    import matplotlib.pyplot as plt
    import numpy as np

    fig, ax = plt.subplots(figsize=(16, 4.8))
    if flags:
        cmap = plt.get_cmap("Greys").copy()
        cmap.set_bad("#ffffff")
        im = ax.imshow(data.T, aspect="auto", cmap=cmap, vmin=0, vmax=1,
                       extent=(-0.5, data.shape[0] - 0.5, depth_max, 0), interpolation="nearest")
        bar = fig.colorbar(im, ax=ax, pad=0.01)
        bar.set_label("Share of cells flagged", fontsize=14)
    else:
        cmap = plt.get_cmap("viridis").copy()
        cmap.set_bad("#ffffff")
        im = ax.imshow(data.T, aspect="auto", cmap=cmap, vmin=SV_RANGE[0], vmax=SV_RANGE[1],
                       extent=(-0.5, data.shape[0] - 0.5, depth_max, 0), interpolation="nearest")
        bar = fig.colorbar(im, ax=ax, pad=0.01)
        bar.set_label("Sv, dB re 1 m⁻¹ (white: removed or no data)", fontsize=13)
    bar.ax.tick_params(labelsize=13)
    if seabed is not None and np.isfinite(seabed).any():
        cols = np.arange(seabed.size)
        ax.plot(cols, seabed, color="white", lw=3.2)
        ax.plot(cols, seabed, color="black", lw=1.4, ls=(0, (4, 2)))
    ax.set_ylabel("Range, m", fontsize=14)
    ax.tick_params(labelsize=13)
    ax.set_title(title, fontsize=16, loc="left")
    _time_axis(ax, times, fp, data.shape[0], 13)
    fig.tight_layout()
    _save(fig, path)


def _open(fs, root: str, container: str, day: str, so: dict):
    from oceanstream.echodata.products import open_product_uri

    for pattern in ("*--masked.zarr", "*--denoised.zarr", f"{day}--*.zarr"):
        found = sorted(fs.glob(f"{root}/{container}/{day}/{pattern}"))
        if found:
            return open_product_uri(f"s3://{found[0]}", storage_options=so), found[0]
    raise SystemExit(f"No store for {day} in {container}")


def render_channels(fs, so, args, runs, out: Path, index: list) -> None:
    import numpy as np

    source, _ = _open(fs, args.root, args.sv_container, args.day, so)
    opened = {label: _open(fs, args.root, container, args.day, so) for label, container in runs}
    for i in range(source.sizes["channel"]):
        khz = _khz(source, i)
        if args.channels and khz not in args.channels:
            continue
        sv_i = source["Sv"].isel(channel=i)
        own = np.flatnonzero(sv_i.notnull().any("range_sample").values)
        if own.size == 0:
            continue
        ranges = source["echo_range"].isel(channel=i, ping_time=int(own[0])).values
        n_range = int(np.flatnonzero(np.isfinite(ranges)).max()) + 1
        depth_max = float(np.nanmax(ranges[:n_range]))
        times = source["ping_time"].values[own]
        fp = -(-own.size // MAX_COLS)
        fr = -(-n_range // MAX_ROWS)
        sel = {"ping_time": own, "range_sample": slice(0, n_range)}
        seabeds = {}
        for label, (ds, _) in opened.items():
            if "seabed_depth" in ds.coords:
                line = ds["seabed_depth"].values[own].astype(float)
                pad = -line.size % fp
                with warnings.catch_warnings():
                    warnings.simplefilter("ignore", RuntimeWarning)
                    seabeds[label] = np.nanmean(
                        np.pad(line, (0, pad), constant_values=np.nan).reshape(-1, fp), axis=1)
        first_line = next(iter(seabeds.values()), None)

        def emit(kind, label, data, **kw):
            name = f"{args.day}_{khz}kHz_{kind}_{len(index):03d}.webp"
            _echogram(out / name, data, depth_max, times, fp, **kw)
            index.append({"day": args.day, "khz": khz, "kind": kind, "run": label,
                          "file": name, "pings": int(own.size)})

        raw = sv_i.isel(sel).values
        finite = np.isfinite(raw)
        note = " · seabed line from the first run with one" if first_line is not None else ""
        emit("sv", args.sv_label, _bin(raw, fp, fr),
             title=f"{args.day} · {khz} kHz · {args.sv_label}{note}", seabed=first_line)
        del raw
        for label, (ds, _) in opened.items():
            values = ds["Sv"].isel(channel=i).isel(sel).values
            emit("sv", label, _bin(values, fp, fr),
                 title=f"{args.day} · {khz} kHz · {label}", seabed=seabeds.get(label))
            del values
            flags = ds["denoise_flags"].isel(channel=i).isel(sel).values
            flagged = np.where(finite, ((flags & TRANSIENT) != 0).astype(float), np.nan)
            if np.nansum(flagged) > 0:
                share = 100 * np.nanmean(flagged)
                emit("transient", label, _bin(flagged, fp, fr, how="share"), flags=True,
                     title=f"{args.day} · {khz} kHz · {label} · transient flags, black "
                           f"({share:.1f} % of cells)")
            del flags, flagged
        print(json.dumps({"day": args.day, "khz": khz, "pings": int(own.size)}), flush=True)


def render_differences(fs, so, args, out: Path, index: list) -> None:
    import matplotlib.pyplot as plt
    import numpy as np
    from matplotlib.colors import ListedColormap
    from matplotlib.patches import Patch

    from oceanstream.echodata.multifrequency import db_difference

    found = sorted(fs.glob(f"{args.root}/{args.mvbs_container}/{args.day}/*--mvbs.zarr"))
    if not found:
        print(json.dumps({"day": args.day, "mvbs": "missing"}), flush=True)
        return
    import xarray as xr

    mvbs = xr.open_zarr(f"s3://{found[0]}", storage_options=so).load()
    keep = mvbs["Sv"].notnull().any(["channel", "depth"]).values
    mvbs = mvbs.isel(ping_time=np.flatnonzero(keep))
    lin = 10 ** (mvbs["Sv"] / 10)
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", RuntimeWarning)
        mean = lin.coarsen(ping_time=DIFF_CELL[0], depth=DIFF_CELL[1], boundary="trim",
                           coord_func={"ping_time": "min", "depth": "mean"}).mean()
    cells = (10 * np.log10(mean)).to_dataset(name="Sv")
    times = cells["ping_time"].values
    step = float(np.median(np.diff(mvbs["ping_time"].values)) / np.timedelta64(1, "s"))
    for high, low, depth_max, above, below in DIFF_PAIRS:
        sub = cells.sel(depth=slice(0, depth_max))
        result = db_difference(sub, freq_low=f"ES{high}", freq_high=f"ES{low}",
                               thr=(FLAT_DB, 60.0))
        diff = result.difference.transpose("ping_time", "depth").values
        dmax = float(sub["depth"].values[-1])
        cell_txt = (f"cells of {DIFF_CELL[0]} × {step:g} s by {DIFF_CELL[1]} m, from MVBS of "
                    f"{args.mvbs_label}")
        fig, ax = plt.subplots(figsize=(16, 4.8))
        cmap = plt.get_cmap("PuOr_r").copy()
        cmap.set_bad("#ffffff")
        im = ax.imshow(diff.T, aspect="auto", cmap=cmap, vmin=-DIFF_RANGE, vmax=DIFF_RANGE,
                       extent=(-0.5, diff.shape[0] - 0.5, dmax, 0), interpolation="nearest")
        bar = fig.colorbar(im, ax=ax, pad=0.01)
        bar.set_label(f"Sv{high} − Sv{low}, dB", fontsize=14)
        bar.ax.tick_params(labelsize=13)
        ax.set_title(f"{args.day} · ΔSv {high} − {low} kHz · orange: {high} kHz stronger, "
                     f"purple: {low} kHz stronger · {cell_txt}", fontsize=13, loc="left")
        ax.set_ylabel("Depth, m", fontsize=14)
        ax.tick_params(labelsize=13)
        _time_axis(ax, times, 1, diff.shape[0], 13)
        fig.tight_layout()
        name = f"{args.day}_dsv_{high}-{low}.webp"
        _save(fig, out / name)
        index.append({"day": args.day, "kind": "dsv", "pair": f"{high}-{low}", "file": name,
                      "run": args.mvbs_label})

        classes = np.full(diff.shape, np.nan)
        classes[diff > FLAT_DB] = 0
        classes[np.abs(diff) <= FLAT_DB] = 1
        classes[diff < -FLAT_DB] = 2
        valid = np.isfinite(diff)
        shares = [100 * float((classes == k).sum()) / max(int(valid.sum()), 1) for k in range(3)]
        labels = [f"black: {above} (ΔSv > +{FLAT_DB:g} dB), {shares[0]:.0f} %",
                  f"grey: flat (|ΔSv| ≤ {FLAT_DB:g} dB), {shares[1]:.0f} %",
                  f"light grey: {below} (ΔSv < −{FLAT_DB:g} dB), {shares[2]:.0f} %"]
        tones = ["#000000", "#808080", "#d0d0d0"]
        fig, ax = plt.subplots(figsize=(16, 5.6))
        cmap = ListedColormap(tones)
        cmap.set_bad("#ffffff")
        ax.imshow(classes.T, aspect="auto", cmap=cmap, vmin=-0.5, vmax=2.5,
                  extent=(-0.5, diff.shape[0] - 0.5, dmax, 0), interpolation="nearest")
        ax.legend(handles=[Patch(facecolor=t, edgecolor="black", label=lab)
                           for t, lab in zip(tones, labels, strict=True)],
                  loc="upper center", bbox_to_anchor=(0.5, -0.2), ncol=1, fontsize=13,
                  frameon=False)
        ax.set_title(f"{args.day} · ΔSv {high} − {low} kHz in three classes · white: no data "
                     f"at one of the two · {cell_txt}", fontsize=13, loc="left")
        ax.set_ylabel("Depth, m", fontsize=14)
        ax.tick_params(labelsize=13)
        _time_axis(ax, times, 1, diff.shape[0], 13)
        fig.tight_layout()
        name = f"{args.day}_dsv_{high}-{low}_classes.webp"
        _save(fig, out / name)
        index.append({"day": args.day, "kind": "dsv_classes", "pair": f"{high}-{low}",
                      "file": name, "run": args.mvbs_label,
                      "shares_pct": [round(s, 1) for s in shares],
                      "fraction_above_flat_by_db_difference": round(result.fraction_in_range, 4)})
        print(json.dumps({"day": args.day, "pair": f"{high}-{low}", "shares_pct": shares}),
              flush=True)


def main() -> int:
    import matplotlib

    matplotlib.use("Agg")
    import s3fs

    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--root", required=True, help="bucket/prefix of the survey, e.g. b/r2r/RR2205")
    p.add_argument("--day", required=True)
    p.add_argument("--sv-container", default="sv")
    p.add_argument("--sv-label", default="Stored Sv, before denoising")
    p.add_argument("--runs", default="",
                   help="'label=container' pairs separated by ';', in display order")
    p.add_argument("--channels", default="", help="kHz to render, e.g. '38,18'; default all")
    p.add_argument("--mvbs-container", default="", help="run whose MVBS to difference")
    p.add_argument("--mvbs-label", default="")
    p.add_argument("--out", required=True)
    args = p.parse_args()
    args.root = args.root.rstrip("/")
    args.channels = [c.strip() for c in args.channels.split(",") if c.strip()]
    runs = [tuple(part.rsplit("=", 1)) for part in args.runs.split(";") if part.strip()]

    so = {"client_kwargs": {"endpoint_url": os.environ["AWS_ENDPOINT_URL"]}}
    fs = s3fs.S3FileSystem(**so)
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    index: list[dict] = []
    render_channels(fs, so, args, runs, out, index)
    if args.mvbs_container:
        render_differences(fs, so, args, out, index)
    (out / "index.json").write_text(json.dumps(index, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
