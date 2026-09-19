"""Checksummed inputs and isolated run directories with an atomic completion marker."""

from __future__ import annotations

import hashlib
import importlib.metadata
import json
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import numpy as np

from oceanstream.coastal.aoi import AOI
from oceanstream.coastal.products import write_json_document
from oceanstream.coastal.scene import Scene

SCHEMA_VERSION = "2.0"


def file_digest(path: str | Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def input_provenance(aoi: AOI, scene: Scene) -> dict[str, Any]:
    inputs: dict[str, Any] = {}
    for name, array in {"rrs_above": scene.rrs_above, "rhos": scene.rhos}.items():
        if array is not None:
            inputs[name] = {
                "sha256": hashlib.sha256(memoryview(np.ascontiguousarray(array))).hexdigest(),
                "shape": array.shape,
                "dtype": str(array.dtype),
            }
    uris = [aoi.deepwater_reference_uri, aoi.attenuation_regions_uri]
    uris.extend(r.uri for r in (aoi.fine_bathymetry, aoi.coarse_bathymetry) if r)
    for uri in uris:
        if uri:
            inputs[uri] = (
                {"sha256": file_digest(uri)}
                if "://" not in uri
                else {"checksum": "unavailable_remote_input"}
            )
    if scene.source_dir:
        for path in sorted(scene.source_dir.glob("*settings*.txt")):
            inputs[str(path)] = {"sha256": file_digest(path)}
    versions = {}
    for package in ("oceanstream", "numpy", "scipy", "rasterio", "xarray", "scikit-learn"):
        try:
            versions[package] = importlib.metadata.version(package)
        except importlib.metadata.PackageNotFoundError:
            versions[package] = "not-installed"
    code = {
        str(p.relative_to(Path(__file__).parent)): file_digest(p)
        for p in sorted(Path(__file__).parent.rglob("*.py"))
    }
    inputs["coastal_source"] = {
        "sha256": hashlib.sha256(json.dumps(code, sort_keys=True).encode()).hexdigest()
    }
    # ACOLITE is external. Never substitute oceanstream's version for its version.
    return {
        "schema_version": SCHEMA_VERSION,
        "inputs": inputs,
        "software": versions,
        "acolite_version": scene.metadata.get("acolite_version", "not_recorded"),
        "scene": scene.describe(),
    }


def new_run_directory(output_dir: str | Path) -> Path:
    if "://" in str(output_dir):
        raise ValueError(
            "The beta processor requires a local output directory for atomic completion. "
            "Sync a completed run to cloud storage afterwards."
        )
    identifier = datetime.now(UTC).strftime("%Y%m%dT%H%M%S") + "-" + uuid.uuid4().hex[:12]
    directory = Path(output_dir).resolve() / "runs" / identifier
    directory.mkdir(parents=True, exist_ok=False)
    return directory


def complete_run(root: Path, directory: Path, manifest: dict[str, Any]) -> None:
    write_json_document(
        directory / "manifest.json",
        {"schema_version": SCHEMA_VERSION, "complete": True, **manifest},
    )
    write_json_document(
        root / "latest.json",
        {"schema_version": SCHEMA_VERSION, "run_dir": str(directory.relative_to(root.resolve()))},
    )


def resolve_run(directory: Path) -> Path:
    pointer = directory / "latest.json"
    if pointer.is_file():
        child = (directory / str(json.loads(pointer.read_text())["run_dir"])).resolve()
        if not child.is_relative_to(directory.resolve()):
            raise ValueError("Run pointer escapes its output directory.")
        return child
    return directory
