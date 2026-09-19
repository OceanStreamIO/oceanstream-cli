"""Register the six supplied scenes without inventing missing scientific inputs.

Run from the root checkout with the coastal extras installed. The output folder
must be new. Geometry comes from existing AOIs and reference depths, never from
retrieved optical products. Registration is exploratory, not a held-out study.
"""

from __future__ import annotations

import argparse
import json
import shutil
from dataclasses import asdict
from pathlib import Path

import numpy as np
import rasterio
from rasterio.features import shapes
from rasterio.warp import transform_geom
from shapely import set_precision
from shapely.geometry import box, mapping, shape
from shapely.ops import unary_union

from oceanstream.coastal import config

DATES = {
    "sesimbra": ("2026-06-17", "2026-06-27"),
    "donegal": ("2026-07-15", "2026-08-24"),
    "summer_isles": ("2026-05-27", "2026-09-09"),
}
FINE = {
    "donegal": "7mLoughSwillyLoughFoyle_depth.tif",
    "summer_isles": "BGS_2005_4_SummerIsles_depth.tif",
}


def write(path: Path, data: object) -> None:
    path.write_text(json.dumps(data, indent=2, allow_nan=False) + "\n")


def collection(geometry: object, properties: dict) -> dict:
    return {
        "type": "FeatureCollection",
        "features": [{"type": "Feature", "geometry": geometry, "properties": properties}],
    }


def register(repo: Path, kelp: Path, output: Path) -> Path:
    output.mkdir(parents=True, exist_ok=False)
    supporting = [str(Path(__file__).resolve())]
    scenes, inventory = [], []
    configs = {
        "retrieval": config.RetrievalConfig(),
        "mask": config.MaskConfig(),
        "bathymetry": config.BathymetryConfig(),
        "attenuation": config.AttenuationConfig(),
        "qc": config.QCConfig(),
        "detectability": config.DetectabilityConfig(),
        "uncertainty": config.UncertaintyConfig(),
    }
    write(output / "config.json", {key: asdict(value) for key, value in configs.items()})
    for site, dates in DATES.items():
        destination = output / site
        destination.mkdir()
        source_aoi = repo / "oceanstream/coastal/aois" / f"{site}.json"
        supporting.append(str(source_aoi))
        aoi = json.loads(source_aoi.read_text())
        fine = (
            kelp / "data/external/emodnet_hr_lidar_sesimbra.tif"
            if site == "sesimbra"
            else repo / ".cache/coastal/bathy" / site / FINE[site]
        )
        coarse = repo / ".cache/coastal/bathy" / site / "emodnet_mean_depth.tif"
        for role, path in (("fine", fine), ("coarse", coarse)):
            if not path.is_file():
                raise FileNotFoundError(path)
            sidecar = path.with_suffix(".provenance.json")
            if not sidecar.exists():
                sidecar = Path(str(path) + ".provenance.json")
            supporting.append(str(sidecar))
            provenance = json.loads(sidecar.read_text())
            ref = aoi[f"{role}_bathymetry"]
            ref["uri"] = str(path)
            ref["provenance"].update(
                local_sidecar=provenance,
                independent_of_scene=True,
                independence_basis=(
                    "Historical EMODnet survey/DTM supplied separately from these 2026 "
                    "Sentinel-2 scenes. This does not assert independence between depth "
                    "products or establish survey accuracy/stability."
                ),
            )
        reporting = aoi["habitat_bbox"] or aoi["metadata"]["screening_bbox"]
        aoi["habitat_bbox"] = reporting
        aoi["metadata"].update(
            regions_preregistered=False,
            reporting_area_provisional=True,
            registration_basis="Existing screening bounds; dates previously inspected.",
            unresolved_inputs=["acquisition-time LAT tide", "measured reference uncertainty"],
        )
        if site == "sesimbra":
            aoi["metadata"].pop("coarse_bathymetry_note", None)
            for name in ("deepwater_reference", "habitat_aoi", "processing_extent"):
                source = kelp / "data/aois" / f"{name}.geojson"
                supporting.append(str(source))
                shutil.copyfile(source, destination / f"{name}.geojson")
            aoi["metadata"]["reporting_geometry_source"] = "habitat_aoi.geojson"
        else:
            # Existing coarse-reference convention is depth >50 m. Subsequent
            # scene preparation still applies all optical-depth/cloud gates.
            with rasterio.open(coarse) as ds:
                depth = ds.read(1, masked=True).filled(np.nan)
                eligible = np.isfinite(depth) & (depth > 50)
                polygons = [
                    shape(transform_geom(ds.crs, "EPSG:4326", geom))
                    for geom, value in shapes(
                        eligible.astype("uint8"), mask=eligible, transform=ds.transform
                    )
                    if value == 1
                ]
            reference = unary_union(polygons).intersection(box(*aoi["processing_bbox"]))
            reference = reference.difference(box(*reporting))
            # Clipping cells on the bbox edge can leave lines/points in a
            # GeometryCollection; only area geometries define raster masks.
            reference = unary_union(
                [
                    geom
                    for geom in getattr(reference, "geoms", [reference])
                    if geom.geom_type in {"Polygon", "MultiPolygon"}
                ]
            )
            # Remove sub-millimetre clipping slivers that otherwise turn into
            # self-intersections under UTM reprojection. This is far below the
            # source DTM and Sentinel-2 resolution; retain the precision in metadata.
            reference = set_precision(reference, 1e-9)
            if reference.is_empty:
                raise ValueError(f"{site}: no offshore reference domain within supplied extent")
            write(
                destination / "deepwater_reference.geojson",
                collection(
                    mapping(reference),
                    {
                        "role": "deepwater_reference",
                        "selection": "coarse depth >50 m outside reporting",
                        "source": str(coarse),
                        "reflectance_inspected_for_selection": False,
                        "is_provisional": True,
                        "coordinate_precision_degrees": 1e-9,
                    },
                ),
            )
            write(
                destination / "habitat_aoi.geojson",
                collection(
                    mapping(box(*reporting)),
                    {
                        "role": "reporting",
                        "source": "existing screening_bbox",
                        "is_provisional": True,
                        "note": "Evaluation rectangle; no habitat-area claim.",
                    },
                ),
            )
        write(
            destination / "attenuation_regions.geojson",
            collection(
                mapping(box(*aoi["metadata"]["screening_bbox"])),
                {
                    "region_id": f"{site}_local",
                    "source": "existing screening_bbox",
                    "is_exploratory": True,
                    "new_heldout_date_required": True,
                },
            ),
        )
        aoi["deepwater_reference_uri"] = "deepwater_reference.geojson"
        aoi["attenuation_regions_uri"] = "attenuation_regions.geojson"
        write(destination / "aoi.json", aoi)
        supporting.extend(
            str(destination / name)
            for name in (
                "habitat_aoi.geojson",
                *(["processing_extent.geojson"] if site == "sesimbra" else []),
            )
        )
        for date in dates:
            scene = (
                kelp / "outputs/s2/timeseries" / date / "s2/acolite"
                if site == "sesimbra"
                else repo / ".cache/coastal/acolite" / site / date
            )
            rasters = sorted(scene.glob("*_rhos_*.tif"))
            if not rasters:
                raise FileNotFoundError(f"No surface reflectance rasters in {scene}")
            with rasterio.open(rasters[0]) as ds:
                tags = ds.tags()
                details = {
                    "shape": list(ds.shape),
                    "crs": str(ds.crs),
                    "acquisition_time": tags.get("NC_GLOBAL#isodate"),
                    "acolite_version": tags.get("NC_GLOBAL#acolite_version"),
                    "available_rhos_files": len(rasters),
                }
            import geopandas as gpd

            for geometry_path in destination.glob("*.geojson"):
                projected = gpd.read_file(geometry_path).to_crs(details["crs"])
                if (
                    not projected.geometry.is_valid.all()
                    or not projected.geom_type.isin(["Polygon", "MultiPolygon"]).all()
                ):
                    raise ValueError(f"Invalid projected region geometry: {geometry_path}")
            if not details["acolite_version"]:
                raise ValueError(f"No recorded ACOLITE version for {scene}")
            scenes.append(
                {
                    "id": f"{site}-{date}",
                    "site": site,
                    "date": date,
                    "aoi": f"{site}/aoi.json",
                    "scene": str(scene),
                    "config": "config.json",
                    "tide": None,
                    "acolite_version": details["acolite_version"],
                }
            )
            inventory.append({"site": site, "date": date, **details})
    write(output / "inventory.json", inventory)
    supporting.append(str(output / "inventory.json"))
    target = output / "benchmark.json"
    write(
        target,
        {
            "schema_version": "1.0",
            "purpose": "diagnostic",
            "description": "Supplied six-scene baseline; experimental optical products.",
            "limitations": [
                "Existing dates are not a new held-out evaluation.",
                "Missing tide/model/reference uncertainty stays flagged; "
                "defaults are sensitivity assumptions.",
                "Different recorded ACOLITE build strings; "
                "equivalence of historical code not established.",
                "No independent optical evidence; "
                "processing completion is not scientific validation.",
            ],
            "depth_bins_m": [0, 5, 10, 20, 40],
            "scenes": scenes,
            "evidence": [],
            "supporting_files": supporting,
        },
    )
    return target


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--kelp-root", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    print(
        register(
            Path(__file__).resolve().parents[2], args.kelp_root.resolve(), args.output.resolve()
        )
    )
