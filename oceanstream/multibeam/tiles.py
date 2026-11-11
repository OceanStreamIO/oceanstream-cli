"""Web map tiles of the campaign grids: shaded depth and greyscale backscatter as PMTiles.

Each layer is reprojected from ``grids/campaign/<var>.vrt`` to Web Mercator one 256-pixel
tile at a time and written as WebP tiles into one PMTiles archive, which a tile server
such as Martin serves as an XYZ layer. Tiles are made only where the grid has tiles,
from ``min_zoom`` to the zoom whose pixel matches the grid cell.

- **depth**, hillshaded (see :class:`Shading`), in one of two colourings:
  ``basemap`` takes the OceanStream basemap's sea colour ramp (ocean-basemap
  ``color-ramps/bathymetry-water.txt``, palette D, adopted 2026-11-10) as it is, so a
  survey reads as a sharper patch of the same map. That ramp changes very little across a
  shelf (16 m and 52 m differ by about 11 levels of luminance), so ``stretch`` spreads its
  colours from 0 to 6,500 m over the survey's own 1st to 99th percentile of depth, re-spaced
  so lightness falls in equal steps with depth (the ramp itself puts a quarter of that range
  in its first 125 m and lightens again by 4 levels between 175 and 350 m).
- **backscatter**: greyscale from the campaign's 5th (black) to 95th (white) percentile,
  light for strong returns. No shading.

Archives: ``tiles/depth-basemap.pmtiles``, ``tiles/depth-stretch.pmtiles`` and
``tiles/backscatter.pmtiles``; the value ranges are in each archive's metadata.
"""

from __future__ import annotations

import io
import json
import math
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np

TILE = 256
ORIGIN = 20037508.342789244  # half the Web Mercator world width, m
EARTH_RADIUS = 6378137.0
LAYERS = ("depth", "backscatter")
DEPTH_COLOURS = ("basemap", "stretch")
STRETCH_TO_M = 6500.0  # the basemap ramp depth that the survey's deepest depths take
WEBP_QUALITY = 90

# The basemap's sea ramp, palette D (ocean-basemap color-ramps/bathymetry-water.txt):
# elevation in metres (negative below the sea surface) and RGB.
SEA_RAMP: tuple[tuple[float, tuple[int, int, int]], ...] = (
    (-11000, (22, 89, 116)), (-10000, (26, 97, 123)), (-8500, (56, 149, 186)),
    (-6500, (70, 158, 194)), (-5750, (82, 172, 211)), (-5250, (88, 175, 216)),
    (-4750, (100, 181, 221)), (-4250, (108, 184, 224)), (-3750, (116, 191, 232)),
    (-3250, (118, 193, 232)), (-2750, (123, 198, 236)), (-2250, (124, 198, 236)),
    (-1750, (127, 202, 240)), (-1250, (130, 203, 240)), (-875, (141, 208, 240)),
    (-625, (149, 213, 240)), (-450, (155, 214, 240)), (-350, (157, 215, 240)),
    (-250, (159, 217, 236)), (-175, (165, 210, 237)), (-125, (174, 210, 240)),
    (-87.5, (186, 216, 243)), (-62.5, (195, 221, 244)), (-35, (204, 226, 245)),
    (-25, (207, 227, 245)), (-17.5, (210, 229, 245)), (-12.5, (212, 230, 245)),
    (-7.5, (213, 231, 245)), (-3.5, (215, 231, 245)), (0, (216, 232, 245)),
)

LUMA = np.array([0.2126, 0.7152, 0.0722])  # Rec. 709 luminance weights

Log = Callable[[str], None]


def _quiet(_: str) -> None:
    pass


# ---------------------------------------------------------------------------
# Web Mercator tiles
# ---------------------------------------------------------------------------


def tile_bounds(z: int, x: int, y: int) -> tuple[float, float, float, float]:
    """(xmin, ymin, xmax, ymax) of an XYZ tile in EPSG:3857 metres."""
    size = 2 * ORIGIN / (1 << z)
    return (-ORIGIN + x * size, ORIGIN - (y + 1) * size, -ORIGIN + (x + 1) * size,
            ORIGIN - y * size)


def lonlat_to_tile(lon: float, lat: float, z: int) -> tuple[int, int]:
    n = 1 << z
    lat = max(min(lat, 85.0511287798066), -85.0511287798066)
    x = int((lon + 180.0) / 360.0 * n)
    y = int((1 - math.asinh(math.tan(math.radians(lat))) / math.pi) / 2 * n)
    return min(max(x, 0), n - 1), min(max(y, 0), n - 1)


def native_zoom(cell_m: float, lat: float) -> int:
    """The Web Mercator zoom whose pixel is nearest a grid cell of *cell_m* metres at *lat*."""
    ground = 2 * math.pi * EARTH_RADIUS * math.cos(math.radians(lat)) / TILE
    return max(0, min(22, round(math.log2(ground / max(cell_m, 1e-6)))))


def tiles_for_bounds(lonlat_boxes: Iterable[tuple[float, float, float, float]],
                     zooms: Sequence[int]) -> set[tuple[int, int, int]]:
    """Every (z, x, y) touching one of the (west, south, east, north) boxes."""
    out: set[tuple[int, int, int]] = set()
    boxes = list(lonlat_boxes)
    for z in zooms:
        for w, s, e, n in boxes:
            x0, y0 = lonlat_to_tile(w, n, z)
            x1, y1 = lonlat_to_tile(e, s, z)
            out.update((z, x, y) for x in range(x0, x1 + 1) for y in range(y0, y1 + 1))
    return out


# ---------------------------------------------------------------------------
# Colour and light (the basemap's, ocean-basemap processing/raster_tiles.py)
# ---------------------------------------------------------------------------


def ramp_colour(elevation: np.ndarray,
                ramp: Sequence[tuple[float, tuple[int, int, int]]] = SEA_RAMP) -> np.ndarray:
    """Float RGB (0-255) of each elevation, clamped beyond the end stops."""
    xs = np.array([s[0] for s in ramp], dtype=np.float64)
    rgb = np.array([s[1] for s in ramp], dtype=np.float64)
    return np.stack([np.interp(elevation, xs, rgb[:, c]) for c in range(3)], axis=-1)


def even_lightness_ramp(
    ramp: Sequence[tuple[float, tuple[int, int, int]]] = SEA_RAMP,
    deepest: float = -STRETCH_TO_M,
    stops: int = 9,
) -> tuple[tuple[float, tuple[int, int, int]], ...]:
    """*ramp* from 0 m to *deepest*, re-spaced so its luminance falls in equal steps.

    Returns ramp stops at positions 0 (shallowest) to -1 (deepest). Where the ramp lightens
    again with depth, its darkest colour so far is kept, so the result never lightens.
    """
    z = np.linspace(0.0, deepest, 4001)
    lum = np.minimum.accumulate(ramp_colour(z, ramp) @ LUMA)
    targets = np.linspace(lum[0], lum[-1], stops)
    at = np.interp(targets[::-1], lum[::-1], z[::-1])[::-1]
    colours = np.round(ramp_colour(at, ramp)).astype(int)
    return tuple(
        sorted((-i / (stops - 1), (int(c[0]), int(c[1]), int(c[2])))
               for i, c in enumerate(colours))
    )


STRETCH_RAMP = even_lightness_ramp()


@dataclass(frozen=True)
class Shading:
    """Hillshade strength.

    The vertical exaggeration grows with the pixel size, ``(pixel_m / reference_m) **
    power`` clamped to at least 1, so relief still reads when zoomed out. The basemap's
    bathymetry uses ``reference_m=30, strength=0.6, floor=0.6, ceiling=1.12``, tuned for
    25-100 m grids: on a 1 m multibeam grid it leaves sand waves and ledges a metre or two
    high nearly invisible. The defaults here exaggerate about 3 times at 1 m pixels, 6 times
    at 4 m (z15) and 35 times at 125 m (z10).
    """

    strength: float = 0.8
    reference_m: float = 0.1
    power: float = 0.5
    azimuth: float = 315.0
    altitude: float = 45.0
    floor: float = 0.55
    ceiling: float = 1.2

    def exaggeration(self, pixel_m: float) -> float:
        return max(float((pixel_m / self.reference_m) ** self.power), 1.0)


def hillshade(elevation: np.ndarray, pixel_m: float, shading: Shading) -> np.ndarray:
    """Light relative to flat ground (1.0) for the inner pixels of a one-pixel-buffered grid."""
    z = elevation * shading.exaggeration(pixel_m)
    dzdy, dzdx = np.gradient(z, pixel_m, pixel_m)
    dzdy = -dzdy  # rows run north to south
    slope = np.arctan(np.hypot(dzdx, dzdy))
    aspect = np.arctan2(-dzdx, -dzdy)
    az, alt = math.radians(shading.azimuth), math.radians(shading.altitude)
    lit = math.sin(alt) * np.cos(slope) + math.cos(alt) * np.sin(slope) * np.cos(az - aspect)
    out: np.ndarray = (lit / math.sin(alt))[1:-1, 1:-1]
    return out


def shade(rgb: np.ndarray, light: np.ndarray, shading: Shading) -> np.ndarray:
    factor = np.clip(1.0 + shading.strength * (light - 1.0), shading.floor, shading.ceiling)
    out: np.ndarray = np.clip(rgb * factor[..., None], 0, 255)
    return out


def _fill_nearest(a: np.ndarray, max_px: float | None = None) -> np.ndarray:
    """Copy of *a* with NaN cells set to the nearest valid cell, within *max_px* if given."""
    from scipy import ndimage  # noqa: PLC0415 - optional (multibeam extra)

    invalid = ~np.isfinite(a)
    if not invalid.any() or invalid.all():
        return a.copy()
    dist, (iy, ix) = ndimage.distance_transform_edt(invalid, return_indices=True)
    out: np.ndarray = a[iy, ix]
    if max_px is not None:
        out = np.where(dist <= max_px, out, np.nan)
    return out


# ---------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class LayerStyle:
    """How one layer is coloured, with the value range it was coloured over."""

    layer: str
    depth_colours: str = "basemap"
    value_range: tuple[float, float] | None = None
    shading: Shading = Shading()

    def rgba(self, values: np.ndarray, pixel_m: float, fill_px: float | None) -> np.ndarray:
        """RGBA of the inner 256 x 256 pixels of a 258 x 258 grid of values (NaN = no data)."""
        inner = values[1:-1, 1:-1]
        if fill_px:
            inner = _fill_nearest(values, fill_px)[1:-1, 1:-1]
        valid = np.isfinite(inner)
        if self.layer == "depth":
            elevation = -_fill_nearest(values)
            if self.depth_colours == "stretch":
                lo, hi = self.value_range or (0.0, STRETCH_TO_M)
                position = -(-elevation[1:-1, 1:-1] - lo) / max(hi - lo, 1e-6)
                colour = ramp_colour(position, STRETCH_RAMP)
            else:
                colour = ramp_colour(elevation[1:-1, 1:-1])
            rgb = shade(colour, hillshade(elevation, pixel_m, self.shading), self.shading)
        else:
            lo, hi = self.value_range or (-40.0, -20.0)
            grey = np.clip((inner - lo) / max(hi - lo, 1e-6), 0, 1) * 255
            rgb = np.repeat(np.nan_to_num(grey)[..., None], 3, axis=-1)
        out = np.zeros((*inner.shape, 4), dtype=np.uint8)
        out[..., :3] = np.round(np.nan_to_num(rgb)).astype(np.uint8)
        out[..., 3] = np.where(valid, 255, 0)
        return out


def encode_webp(rgba: np.ndarray, quality: int = WEBP_QUALITY) -> bytes:
    from PIL import Image  # noqa: PLC0415 - optional (matplotlib's)

    buf = io.BytesIO()
    Image.fromarray(rgba, "RGBA").save(buf, "WEBP", quality=quality, method=4)
    return buf.getvalue()


def _source_boxes(vrt: Path) -> list[tuple[float, float, float, float]]:
    """(west, south, east, north) of each grid tile next to *vrt* (``<var>/E*_N*.tif``)."""
    import rasterio  # noqa: PLC0415 - optional (multibeam extra)
    from rasterio.warp import transform_bounds  # noqa: PLC0415

    boxes = []
    for tif in sorted((vrt.parent / vrt.stem).glob("E*_N*.tif")):
        with rasterio.open(tif) as src:
            boxes.append(transform_bounds(src.crs, "EPSG:4326", *src.bounds, densify_pts=21))
    if not boxes:
        raise FileNotFoundError(f"No grid tiles next to {vrt}.")
    return boxes


def render_layer(  # noqa: PLR0913, PLR0915
    vrt: Path,
    dst: Path,
    style: LayerStyle,
    *,
    min_zoom: int = 8,
    max_zoom: int | None = None,
    name: str = "",
    description: str = "",
    attribution: str = "",
    log: Log = _quiet,
) -> dict[str, Any]:
    """Render one grid VRT into a PMTiles archive of WebP tiles; return its statistics."""
    import rasterio  # noqa: PLC0415 - optional (multibeam extra)
    from affine import Affine  # noqa: PLC0415
    from pmtiles.tile import Compression, TileType, zxy_to_tileid  # noqa: PLC0415
    from pmtiles.writer import Writer  # noqa: PLC0415
    from rasterio.warp import Resampling, reproject  # noqa: PLC0415

    boxes = _source_boxes(vrt)
    west, south = min(b[0] for b in boxes), min(b[1] for b in boxes)
    east, north = max(b[2] for b in boxes), max(b[3] for b in boxes)
    mid_lat = (south + north) / 2
    with rasterio.open(vrt) as src:
        cell_m = abs(src.res[0])
    native = native_zoom(cell_m, mid_lat)
    top = native if max_zoom is None else max_zoom
    zooms = list(range(min(min_zoom, top), top + 1))
    todo = sorted(tiles_for_bounds(boxes, zooms), key=lambda t: zxy_to_tileid(*t))
    log(f"{vrt.name}: {len(todo)} candidate tiles, z{zooms[0]}-{zooms[-1]} "
        f"(grid cell {cell_m:g} m, native z{native})")

    dst.parent.mkdir(parents=True, exist_ok=True)
    written: dict[int, int] = {}
    size = 0
    buffer = np.full((TILE + 2, TILE + 2), np.nan, dtype=np.float32)
    with rasterio.open(vrt) as src, open(dst, "wb") as f:
        writer = Writer(f)
        for z, x, y in todo:
            xmin, _, xmax, ymax = tile_bounds(z, x, y)
            res = (xmax - xmin) / TILE
            buffer.fill(np.nan)
            reproject(
                source=rasterio.band(src, 1), destination=buffer,
                src_nodata=np.nan, dst_nodata=np.nan,
                dst_transform=Affine(res, 0, xmin - res, 0, -res, ymax + res),
                dst_crs="EPSG:3857",
                resampling=Resampling.bilinear if z >= native else Resampling.average,
            )
            if not np.isfinite(buffer[1:-1, 1:-1]).any():
                continue
            lat = math.degrees(math.atan(math.sinh((ymax - res * TILE / 2) / EARTH_RADIUS)))
            pixel_m = res * math.cos(math.radians(lat))
            rgba = style.rgba(buffer, pixel_m, 1.0 if z >= native else None)
            data = encode_webp(rgba)
            writer.write_tile(zxy_to_tileid(z, x, y), data)
            written[z] = written.get(z, 0) + 1
            size += len(data)
        if not written:
            raise RuntimeError(f"{vrt} rendered no tiles")
        zmin, zmax = min(written), max(written)
        info = {
            "layer": style.layer,
            "depth_colours": style.depth_colours if style.layer == "depth" else None,
            "value_range": list(style.value_range) if style.value_range else None,
            "native_zoom": native,
            "tiles_per_zoom": {str(k): v for k, v in sorted(written.items())},
        }
        writer.finalize(
            {
                "tile_type": TileType.WEBP, "tile_compression": Compression.NONE,
                "min_zoom": zmin, "max_zoom": zmax,
                "min_lon_e7": int(west * 1e7), "min_lat_e7": int(south * 1e7),
                "max_lon_e7": int(east * 1e7), "max_lat_e7": int(north * 1e7),
                "center_zoom": min(zmax, max(zmin, native - 3)),
                "center_lon_e7": int((west + east) / 2 * 1e7),
                "center_lat_e7": int(mid_lat * 1e7),
            },
            {
                "name": name or dst.stem, "description": description, "attribution": attribution,
                "format": "webp", "type": "overlay", "minzoom": zmin, "maxzoom": zmax,
                "bounds": [west, south, east, north], "oceanstream": info,
            },
        )
    log(f"{dst.name}: {sum(written.values())} tiles, {size / 1e6:.1f} MB")
    return {"path": str(dst), "tiles": sum(written.values()), "bytes": size,
            "bounds": [west, south, east, north], **info}


def _campaign_ranges(output_dir: Path) -> tuple[tuple[float, float] | None,
                                                  tuple[float, float] | None]:
    """Depth 1st-99th and backscatter 5th-95th percentiles from summaries/campaign.json."""
    path = output_dir / "summaries" / "campaign.json"
    if not path.exists():
        return None, None
    s = json.loads(path.read_text())
    d, b = s.get("cell_depth_m") or {}, s.get("cell_backscatter_db") or {}
    depth = (d["p1"], d["p99"]) if d.get("p1") is not None and d.get("p99") is not None else None
    bs = (b["p5"], b["p95"]) if b.get("p5") is not None and b.get("p95") is not None else None
    return depth, bs


def render_tiles(  # noqa: PLR0913
    output_dir: Path,
    *,
    layers: Sequence[str] = LAYERS,
    depth_colours: Sequence[str] = ("stretch",),
    min_zoom: int = 8,
    max_zoom: int | None = None,
    name: str = "",
    log: Log = _quiet,
) -> list[dict[str, Any]]:
    """Render the campaign mosaic into ``output_dir/tiles``.

    One archive per layer: ``depth-<colours>.pmtiles`` for each of *depth_colours*
    (``basemap``, ``stretch``) and ``backscatter.pmtiles``.
    """
    output_dir = Path(output_dir)
    unknown = set(layers) - set(LAYERS)
    if unknown:
        raise ValueError(f"Unknown tile layer(s) {sorted(unknown)}; choose from {LAYERS}.")
    bad = set(depth_colours) - set(DEPTH_COLOURS)
    if bad or not depth_colours:
        raise ValueError(f"Depth colours must be among {DEPTH_COLOURS}, not {sorted(bad)}.")
    grid_dir = output_dir / "grids" / "campaign"
    depth_range, bs_range = _campaign_ranges(output_dir)
    jobs: list[tuple[str, LayerStyle, str]] = []
    for layer in layers:
        if not (grid_dir / f"{layer}.vrt").exists():
            raise FileNotFoundError(
                f"No {grid_dir / f'{layer}.vrt'}. Build the campaign mosaic first "
                "(`oceanstream process multibeam --mosaic`)."
            )
        if layer == "backscatter":
            jobs.append(("backscatter", LayerStyle("backscatter", value_range=bs_range),
                         "Multibeam backscatter, greyscale, light for strong returns"
                         + (f", {bs_range[0]:.1f} dB black to {bs_range[1]:.1f} dB white"
                            if bs_range else "")))
            continue
        for colours in dict.fromkeys(depth_colours):
            style = LayerStyle("depth", colours, depth_range)
            if colours == "basemap":
                how = "in the colours of the OceanStream basemap's sea ramp"
            elif depth_range:
                how = (f"with the basemap's sea ramp stretched over {depth_range[0]:.1f}-"
                       f"{depth_range[1]:.1f} m")
            else:
                how = "with the basemap's sea ramp stretched"
            jobs.append((f"depth-{colours}", style,
                         f"Multibeam depth below the water line, hillshaded, {how}"))
    out = []
    for stem, style, what in jobs:
        out.append(render_layer(
            grid_dir / f"{style.layer}.vrt", output_dir / "tiles" / f"{stem}.pmtiles", style,
            min_zoom=min_zoom, max_zoom=max_zoom, name=f"{name} {stem}".strip(),
            description=what, log=log,
        ))
    return out
