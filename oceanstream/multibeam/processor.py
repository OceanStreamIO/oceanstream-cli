"""Multibeam processing: Kongsberg .all files to soundings, bathymetry and backscatter grids.

Output layout under ``output_dir``::

    grid.json                     CRS, cell size and tile size shared by every grid
    soundings/<day>.parquet       every valid sounding: time, beam, position, depth, backscatter
    grids/days/<day>/<var>/*.tif  1000 x 1000-cell COG tiles; <var>.vrt mosaics them
    grids/campaign/...            the same, combined over all days (``--mosaic``)
    quicklooks/<day|campaign>/    greyscale PNGs of depth and backscatter
    summaries/<day|campaign>.json counts, depth and backscatter statistics, angular response

Depths are below the water line at each ping (transducer depth from the XYZ88 datagram),
with the sound speed applied at sea; there is no tide reduction.
"""

from __future__ import annotations

import json
import os
import shutil
from collections.abc import Callable, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

import numpy as np
import pyarrow as pa
import pyarrow.parquet as pq

from .backscatter import AngularResponse
from .grid import (
    VARIABLES,
    GridAccumulator,
    GridSpec,
    TileGrid,
    tile_grid_from_files,
    write_tiles,
    write_vrts,
)
from .quicklook import write_quicklooks
from .reader import library_version, read_kongsberg_all
from .soundings import SoundingBatch, georeference, utm_epsg

if TYPE_CHECKING:
    from ..providers.base import ProviderBase

DEPTH_REFERENCE = (
    "Below the water line at each ping: XYZ88 depth plus the transmit transducer depth it "
    "records. Sound speed as applied at sea; no tide reduction."
)
Log = Callable[[str], None]
MIN_STD_SOUNDINGS_STATS = 3  # cells with at least this many soundings enter std statistics
BATCH_ROWS = 10_000_000  # soundings gridded at a time
SAMPLE_STEP = 20  # every n-th sounding enters the sounding-level percentiles


def _quiet(_: str) -> None:
    pass


def find_all_files(input_dir: Path) -> list[Path]:
    """Kongsberg .all files under ``input_dir`` (recursive), sorted by name."""
    files = sorted(p for p in Path(input_dir).rglob("*.all") if p.is_file())
    if not files and any(Path(input_dir).rglob("*.all*.gz")):
        raise FileNotFoundError(
            f"{input_dir} holds only gzipped .all files. Unzip them first, and drop "
            "MB-System suffixes: NCEI's `x.all.mb58.gz` becomes `x.all`."
        )
    return files


def _write_json(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=1) + "\n")


def _load_spec(output_dir: Path) -> GridSpec | None:
    p = output_dir / "grid.json"
    if not p.exists():
        return None
    d = json.loads(p.read_text())
    return GridSpec(epsg=int(d["epsg"]), resolution=float(d["resolution_m"]),
                    tile_px=int(d["tile_px"]))


def _save_spec(output_dir: Path, spec: GridSpec) -> None:
    _write_json(output_dir / "grid.json", {
        "epsg": spec.epsg, "resolution_m": spec.resolution, "tile_px": spec.tile_px,
        "depth_reference": DEPTH_REFERENCE,
    })


def _check_spec(existing: GridSpec | None, wanted: GridSpec, output_dir: Path) -> None:
    if existing is not None and existing != wanted:
        raise ValueError(
            f"{output_dir}/grid.json has EPSG {existing.epsg}, {existing.resolution} m cells "
            f"and {existing.tile_px}-cell tiles, but this run asks for EPSG {wanted.epsg}, "
            f"{wanted.resolution} m and {wanted.tile_px}. Use the same --epsg, --resolution "
            "and --tile-size for every day of a campaign, or another --output-dir."
        )


def _percentiles(values: np.ndarray, qs: Sequence[float]) -> dict[str, float | None]:
    v = values[np.isfinite(values)]
    if v.size == 0:
        return {f"p{q:g}": None for q in qs}
    return {f"p{q:g}": round(float(x), 3) for q, x in zip(qs, np.percentile(v, qs), strict=True)}


class _DayWriter:
    """Streams one day's soundings to Parquet and keeps its running statistics."""

    def __init__(self, path: Path, epsg: int) -> None:
        self.path = path
        self.tmp = path.with_suffix(".parquet.part")
        path.parent.mkdir(parents=True, exist_ok=True)
        self.schema = pa.schema([
            ("ping_time", pa.timestamp("ns", tz="UTC")),
            ("beam", pa.uint16()),
            ("latitude", pa.float64()),
            ("longitude", pa.float64()),
            ("easting", pa.float64()),
            ("northing", pa.float64()),
            ("depth_m", pa.float32()),
            ("backscatter_db", pa.float32()),
            ("beam_angle_deg", pa.float32()),
            ("quality_factor", pa.uint8()),
        ], metadata={"crs": f"EPSG:{epsg}", "depth_reference": DEPTH_REFERENCE})
        self.writer = pq.ParquetWriter(self.tmp, self.schema, compression="zstd")
        self.pings = 0
        self.soundings = 0
        self.t0: np.datetime64 | None = None
        self.t1: np.datetime64 | None = None

    def write(  # noqa: PLR0913
        self, b: SoundingBatch, *, lat: np.ndarray, lon: np.ndarray, e: np.ndarray,
        n: np.ndarray, depth: np.ndarray,
    ) -> None:
        cols = [
            pa.array(b.ping_time, type=pa.timestamp("ns")).cast(pa.timestamp("ns", tz="UTC")),
            pa.array(b.beam), pa.array(lat), pa.array(lon), pa.array(e), pa.array(n),
            pa.array(depth.astype(np.float32)), pa.array(b.backscatter_db),
            pa.array(b.beam_angle_deg), pa.array(b.quality_factor),
        ]
        self.writer.write_table(pa.Table.from_arrays(cols, schema=self.schema))
        self.pings += int(np.unique(b.ping_time).size)
        self.soundings += len(b)
        lo, hi = b.ping_time.min(), b.ping_time.max()
        self.t0 = lo if self.t0 is None else min(self.t0, lo)
        self.t1 = hi if self.t1 is None else max(self.t1, hi)

    def close(self) -> None:
        self.writer.close()
        self.tmp.replace(self.path)

    def meta(self, files: Sequence[Path]) -> dict[str, Any]:
        return {"files": [p.name for p in files], "pings": self.pings,
                "soundings": self.soundings, "time_start": str(self.t0),
                "time_end": str(self.t1)}


def _grid_one(
    output_dir: Path, scope: str, spec: GridSpec, tiles: Any, *, quicklook: bool
) -> dict[str, Any]:
    """Write tiles from an iterator of (ti, tj, TileGrid); return cell statistics."""
    grid_dir = output_dir / "grids" / scope
    if grid_dir.exists():
        shutil.rmtree(grid_dir)
    depth_cells, std_cells, bs_cells = [], [], []
    n_tiles = 0
    for ti, tj, g in tiles:
        write_tiles(grid_dir, spec, ti, tj, g)
        a = g.arrays(spec.tile_px)
        has = a["count"] > 0
        depth_cells.append(a["depth"][has])
        std_cells.append(a["depth_std"][a["count"] >= MIN_STD_SOUNDINGS_STATS])
        bs_cells.append(a["backscatter"][has])
        n_tiles += 1
    write_vrts(grid_dir, spec)
    depth = np.concatenate(depth_cells) if depth_cells else np.empty(0)
    std = np.concatenate(std_cells) if std_cells else np.empty(0)
    bs = np.concatenate(bs_cells) if bs_cells else np.empty(0)
    name = scope.rsplit("/", 1)[-1]
    if quicklook and n_tiles:
        write_quicklooks(grid_dir, output_dir / "quicklooks" / name, name, spec)
    return {
        "tiles": n_tiles,
        "cells_with_data": int(depth.size),
        "area_km2": round(depth.size * spec.resolution**2 / 1e6, 4),
        "cell_depth_m": _percentiles(depth, (1, 50, 99)),
        "cell_depth_std_m_where_3_or_more_soundings": _percentiles(std, (50, 90, 99)),
        "cell_backscatter_db": _percentiles(bs, (5, 50, 95)),
    }


def process_files(  # noqa: PLR0913
    files: Sequence[Path],
    output_dir: Path,
    *,
    days: Sequence[str] | None = None,
    resolution: float = 1.0,
    tile_px: int = 1000,
    epsg: int | None = None,
    quicklook: bool = True,
    batches: Any = None,
    log: Log = _quiet,
) -> list[dict[str, Any]]:
    """Process .all files into per-day soundings, grids, quick-looks and summaries.

    Pings are assigned to their UTC day; ``days`` keeps only those. ``batches`` replaces
    the file reader with any iterable of :class:`SoundingBatch` (used by tests).
    """
    from pyproj import Transformer  # noqa: PLC0415 - optional

    output_dir = Path(output_dir)
    existing = _load_spec(output_dir)
    spec: GridSpec | None = None
    transformer = None
    writers: dict[str, _DayWriter] = {}
    source = batches if batches is not None else read_kongsberg_all(files, days=days)
    for b in source:
        lat, lon, depth = georeference(b)
        if spec is None:
            code = epsg or (existing.epsg if existing else
                            utm_epsg(float(np.median(lat)), float(np.median(lon))))
            spec = GridSpec(epsg=code, resolution=resolution, tile_px=tile_px)
            _check_spec(existing, spec, output_dir)
            transformer = Transformer.from_crs("EPSG:4326", f"EPSG:{code}", always_xy=True)
            log(f"grids in EPSG:{code}, {resolution} m cells, {tile_px}-cell tiles")
        e, n = transformer.transform(lon, lat)  # type: ignore[union-attr]
        day_of = b.ping_time.astype("datetime64[D]")
        for d in np.unique(day_of):
            m = day_of == d
            key = str(d)
            if key not in writers:
                writers[key] = _DayWriter(output_dir / "soundings" / f"{key}.parquet", spec.epsg)
                log(f"{key}: reading")
            sel = b.select(m)
            writers[key].write(sel, lat=lat[m], lon=lon[m], e=np.asarray(e)[m],
                               n=np.asarray(n)[m], depth=depth[m])
    if spec is None:
        log("no valid soundings found")
        return []
    _save_spec(output_dir, spec)
    summaries = []
    for day, w in sorted(writers.items()):
        w.close()
        log(f"{day}: {w.pings} pings, {w.soundings} soundings; gridding")
        summaries.append(_grid_day(output_dir, day, w.path, spec, meta=w.meta(files),
                                   quicklook=quicklook))
    return summaries


def regrid_days(output_dir: Path, *, days: Sequence[str] | None = None,
                quicklook: bool = True, log: Log = _quiet) -> list[dict[str, Any]]:
    """Grid the soundings already in ``output_dir/soundings`` again, without the raw files.

    For a change of the backscatter correction or the gridding; the CRS, cell size and
    tile size stay those of ``grid.json``. File, ping and time details are carried over
    from the day's existing summary.
    """
    output_dir = Path(output_dir)
    spec = _load_spec(output_dir)
    if spec is None:
        raise FileNotFoundError(f"No grid.json in {output_dir}; process the .all files first.")
    paths = sorted((output_dir / "soundings").glob("*.parquet"))
    if days:
        paths = [p for p in paths if p.stem in set(days)]
    if not paths:
        raise FileNotFoundError(
            f"No soundings/<day>.parquet in {output_dir} for {days or 'any day'}."
        )
    out = []
    for p in paths:
        old = output_dir / "summaries" / f"{p.stem}.json"
        prev = json.loads(old.read_text()) if old.exists() else {}
        meta = {k: prev.get(k) for k in ("files", "pings", "time_start", "time_end")}
        meta["soundings"] = pq.ParquetFile(p).metadata.num_rows
        log(f"{p.stem}: regridding {meta['soundings']} soundings")
        out.append(_grid_day(output_dir, p.stem, p, spec, meta=meta, quicklook=quicklook))
    return out


def _grid_day(  # noqa: PLR0913
    output_dir: Path, day: str, soundings: Path, spec: GridSpec, *,
    meta: dict[str, Any], quicklook: bool,
) -> dict[str, Any]:
    """Grid one day's soundings: angular response first, then the grid, in batches."""
    pf = pq.ParquetFile(soundings)
    response = AngularResponse()
    for rb in pf.iter_batches(batch_size=BATCH_ROWS, columns=["beam_angle_deg", "backscatter_db"]):
        response.add(rb.column("beam_angle_deg").to_numpy(zero_copy_only=False),
                     rb.column("backscatter_db").to_numpy(zero_copy_only=False))
    acc = GridAccumulator(spec)
    depth_sample, bs_sample = [], []
    columns = ["easting", "northing", "depth_m", "backscatter_db", "beam_angle_deg"]
    for rb in pf.iter_batches(batch_size=BATCH_ROWS, columns=columns):
        col = {c: rb.column(c).to_numpy(zero_copy_only=False) for c in columns}
        bs_flat = response.flatten(col["beam_angle_deg"], col["backscatter_db"])
        acc.add(col["easting"], col["northing"], col["depth_m"], bs_flat)
        depth_sample.append(col["depth_m"][::SAMPLE_STEP])
        bs_sample.append(col["backscatter_db"][::SAMPLE_STEP])
    depth = np.concatenate(depth_sample) if depth_sample else np.empty(0, np.float32)
    bs_raw = np.concatenate(bs_sample) if bs_sample else np.empty(0, np.float32)
    stats = _grid_one(output_dir, f"days/{day}", spec, acc.drain(), quicklook=quicklook)
    summary = {
        "day": day,
        "created": datetime.now(UTC).isoformat(timespec="seconds"),
        "software": {"themachinethatgoesping": library_version(),
                     "oceanstream_revision": os.environ.get("OCEANSTREAM_REVISION")},
        **meta,
        "epsg": spec.epsg,
        "resolution_m": spec.resolution,
        "tile_px": spec.tile_px,
        "depth_reference": DEPTH_REFERENCE,
        "sounding_depth_m_every_20th": _percentiles(depth.astype(np.float64), (1, 50, 99)),
        "sounding_backscatter_db_every_20th": _percentiles(bs_raw.astype(np.float64),
                                                           (5, 50, 95)),
        "angular_response": response.to_dict(),
        **stats,
    }
    _write_json(output_dir / "summaries" / f"{day}.json", summary)
    return summary


def build_mosaic(output_dir: Path, *, quicklook: bool = True,
                 log: Log = _quiet) -> dict[str, Any]:
    """Combine every day's grid in ``output_dir`` into ``grids/campaign``.

    Depth is the sounding-weighted mean over days, backscatter the weighted mean in
    linear units, and depth_std includes the differences between days.
    """
    output_dir = Path(output_dir)
    spec = _load_spec(output_dir)
    days_dir = output_dir / "grids" / "days"
    day_dirs = sorted(p for p in days_dir.iterdir() if p.is_dir()) if days_dir.exists() else []
    if spec is None or not day_dirs:
        raise FileNotFoundError(
            f"No day grids in {days_dir}. Run `oceanstream process multibeam` on the days first."
        )
    names = sorted({p.name for d in day_dirs for p in (d / "count").glob("*.tif")})
    log(f"campaign mosaic of {len(day_dirs)} days, {len(names)} tiles")

    def tiles() -> Any:
        for name in names:
            ti, tj = spec.tile_index(name)
            g = TileGrid.empty(spec.tile_px * spec.tile_px)
            for d in day_dirs:
                if (d / "count" / name).exists():
                    g.add(tile_grid_from_files(d, name))
            yield ti, tj, g

    stats = _grid_one(output_dir, "campaign", spec, tiles(), quicklook=quicklook)
    if quicklook:
        # Redraw the days too, so they follow the current quick-look layout.
        for d in day_dirs:
            write_quicklooks(d, output_dir / "quicklooks" / d.name, d.name, spec)
    summary = {
        "scope": "campaign",
        "created": datetime.now(UTC).isoformat(timespec="seconds"),
        "days": [d.name for d in day_dirs],
        "epsg": spec.epsg,
        "resolution_m": spec.resolution,
        "tile_px": spec.tile_px,
        "depth_reference": DEPTH_REFERENCE,
        "variables": list(VARIABLES),
        **stats,
    }
    _write_json(output_dir / "summaries" / "campaign.json", summary)
    return summary


def process(  # noqa: PLR0913
    provider: ProviderBase,
    input_dir: Path | None,
    output_dir: Path,
    *,
    days: Sequence[str] | None = None,
    resolution: float = 1.0,
    tile_px: int = 1000,
    epsg: int | None = None,
    quicklook: bool = True,
    mosaic: bool = False,
    regrid: bool = False,
    tiles: bool = False,
    depth_colours: Sequence[str] = ("stretch",),
    min_zoom: int = 8,
    verbose: bool = False,
    dry_run: bool = False,
) -> None:
    """CLI entry: process .all files, regrid stored soundings or build the mosaic, and
    with ``tiles`` render the campaign mosaic's web tiles afterwards (or alone)."""
    log: Log = (lambda m: print(f"[multibeam] {m}", flush=True)) if verbose else _quiet
    if tiles and input_dir is None and not (mosaic or regrid):
        _tiles(output_dir, depth_colours, min_zoom, dry_run, log)
        return
    _process(provider, input_dir, output_dir, days=days, resolution=resolution,
             tile_px=tile_px, epsg=epsg, quicklook=quicklook, mosaic=mosaic, regrid=regrid,
             dry_run=dry_run, log=log)
    if tiles:
        _tiles(output_dir, depth_colours, min_zoom, dry_run, log)


def _tiles(output_dir: Path, depth_colours: Sequence[str], min_zoom: int, dry_run: bool,
           log: Log) -> None:
    if dry_run:
        print(f"[multibeam] Dry Run Summary: would render web tiles of {output_dir}"
              f"/grids/campaign (depth {', '.join(depth_colours)}, backscatter) from "
              f"z{min_zoom} into {output_dir}/tiles")
        return
    from .tiles import render_tiles  # noqa: PLC0415 - needs pmtiles and scipy

    for t in render_tiles(output_dir, depth_colours=depth_colours, min_zoom=min_zoom, log=log):
        zooms = sorted(int(z) for z in t["tiles_per_zoom"])
        print(f"[multibeam] {Path(t['path']).name}: {t['tiles']} tiles, z{zooms[0]}-{zooms[-1]}, "
              f"{t['bytes'] / 1e6:.1f} MB")


def _process(  # noqa: PLR0913
    provider: ProviderBase,
    input_dir: Path | None,
    output_dir: Path,
    *,
    days: Sequence[str] | None,
    resolution: float,
    tile_px: int,
    epsg: int | None,
    quicklook: bool,
    mosaic: bool,
    regrid: bool,
    dry_run: bool,
    log: Log,
) -> None:
    if regrid:
        if dry_run:
            print("[multibeam] Dry Run Summary: would regrid the soundings in "
                  f"{output_dir}/soundings for {', '.join(days) if days else 'every day'}")
            return
        for s in regrid_days(output_dir, days=days, quicklook=quicklook, log=log):
            print(f"[multibeam] {s['day']}: regridded, {s['area_km2']} km2 in {s['tiles']} tiles")
        return
    if mosaic:
        if dry_run:
            print("[multibeam] Dry Run Summary: would combine the day grids in "
                  f"{output_dir}/grids/days into {output_dir}/grids/campaign")
            return
        s = build_mosaic(output_dir, quicklook=quicklook, log=log)
        print(f"[multibeam] campaign mosaic: {s['tiles']} tiles, {s['area_km2']} km2 "
              f"-> {output_dir}/grids/campaign")
        return
    if input_dir is None:
        raise ValueError("--input-dir is required unless --mosaic, --regrid or --tiles is given.")
    files = find_all_files(input_dir)
    if dry_run:
        print("[multibeam] Dry Run Summary")
        print(f"  Source directory : {input_dir} ({len(files)} .all files)")
        print(f"  Output directory : {output_dir}")
        print(f"  Days             : {', '.join(days) if days else 'all in the files'}")
        print(f"  Grid             : {resolution} m cells, {tile_px}-cell tiles, "
              f"EPSG {epsg or 'from the data (UTM)'}")
        print(f"  Reader           : themachinethatgoesping {library_version() or 'NOT INSTALLED'}")
        print(f"  Provider         : {provider.name}")
        return
    if not files:
        raise FileNotFoundError(f"No .all files under {input_dir}.")
    log(f"{len(files)} .all files")
    out = process_files(files, output_dir, days=days, resolution=resolution, tile_px=tile_px,
                        epsg=epsg, quicklook=quicklook, log=log)
    for s in out:
        print(f"[multibeam] {s['day']}: {s['pings']} pings, {s['soundings']} soundings, "
              f"{s['area_km2']} km2 in {s['tiles']} tiles")
