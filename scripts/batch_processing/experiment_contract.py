"""Frozen contract for the 3-preset denoise sensitivity case study.

Phase 0 of ``plan-denoisePresetComparison``: everything that must be pinned
*before* any preset runs — source fingerprints, artifact schemas, numerical
tolerances, and the provenance capture used by every ``run-manifest.json``.

The module is dependency-light on purpose (stdlib + numpy/xarray only when
actually inspecting data) so it can be imported from the runner, the pipeline,
and the report generator without dragging in the processing stack.

Usage::

    python -m experiment_contract fingerprint \\
        --source-root ~/oceanstream_experiment/tpos_saildrone_2023/local-raw-10oct \\
        --day 2023-10-10 \\
        --out experiment-manifest.json

    python -m experiment_contract verify --manifest experiment-manifest.json
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import platform
import subprocess
import sys
import tempfile
from collections.abc import Iterable
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

# ── Schema versions ────────────────────────────────────────────────────────
# Bump when the *shape* of an artifact changes. Consumers must refuse to read
# an artifact whose major schema version they do not know.

EXPERIMENT_MANIFEST_SCHEMA = "oceanstream.experiment-manifest/v1"
RUN_MANIFEST_SCHEMA = "oceanstream.run-manifest/v1"
DENOISE_STATS_SCHEMA = "oceanstream.denoise-stats/v1"
COMPARISON_METRICS_SCHEMA = "oceanstream.comparison-metrics/v1"

REPO_ROOT = Path(__file__).resolve().parents[2]


# ── Numerical tolerances (declared before any results are seen) ────────────


@dataclass(frozen=True)
class Tolerances:
    """Numerical tolerances for every cross-preset comparison.

    Declared up front so no threshold is chosen after seeing results.
    """

    #: Absolute tolerance for Sv / MVBS comparisons, in dB. Below the
    #: calibration uncertainty of the EK80 (~0.5 dB), so a difference under
    #: this is reported as "no difference".
    sv_atol_db: float = 0.1

    #: Relative tolerance for NASC, compared in linear space (m² nmi⁻²).
    nasc_rtol: float = 1e-6

    #: Absolute tolerance for latitude/longitude agreement, in degrees.
    #: 1e-6° ≈ 0.11 m — far below GPS precision.
    coord_atol_deg: float = 1e-6

    #: Absolute tolerance for depth-grid agreement, in metres.
    depth_atol_m: float = 1e-6

    #: ``ping_time`` alignment tolerance for common-support comparison.
    time_atol_ns: int = 0

    #: NaN policy for every comparison: NaN in one arm and a finite value in
    #: another is a *difference*, never "equal". Cells non-finite in all arms
    #: are excluded from the common-support statistics and counted separately.
    equal_nan: bool = False

    #: Frequency-specific integration depth bands (metres), fixed before
    #: results are seen. 38 kHz reaches the full mesopelagic; 200 kHz is
    #: absorption-limited to roughly the upper 250 m.
    integration_bands_m: dict = field(
        default_factory=lambda: {
            "38000": [10.0, 1000.0],
            "200000": [10.0, 250.0],
        }
    )

    #: Bin 0 of the MVBS/NASC depth grid under-integrates because bins start
    #: at 0 m while data starts at the transducer depth. Excluded from every
    #: integrated comparison. See ``oceanstream.echodata.compute.mvbs``.
    exclude_surface_bin: bool = True

    #: Fixed shared colour scale (dB) for every Sv/MVBS echogram across all
    #: presets, so figures are visually comparable.
    sv_color_limits_db: tuple = (-95.0, -50.0)

    #: Symmetric colour limit (dB) for preset-vs-baseline difference panels.
    sv_diff_limit_db: float = 10.0

    def to_dict(self) -> dict:
        d = asdict(self)
        d["sv_color_limits_db"] = list(self.sv_color_limits_db)
        return d


TOLERANCES = Tolerances()


# ── Preset registry ────────────────────────────────────────────────────────

SCRIPT_DIR = Path(__file__).resolve().parent


@dataclass(frozen=True)
class Preset:
    """One arm of the comparison: preset id → TOML → output container."""

    key: str
    label: str
    toml_name: str
    container: str
    lineage: str

    @property
    def toml_path(self) -> Path:
        return SCRIPT_DIR / self.toml_name


#: The three arms. ``ryan-inspired`` is deliberately *not* called "Ryan 2015":
#: the repository preset adapts Ryan's parameters but ORs the masks instead of
#: applying them as a sequential cascade, and uses a Fielding-style transient
#: detector. See the report's "Deviations from Ryan 2015" section.
PRESETS: tuple[Preset, ...] = (
    Preset(
        key="ryan-inspired",
        label="Ryan-inspired (repository preset)",
        toml_name="ryan2015_denoise_defaults.toml",
        container="local-raw-10oct-ryan",
        lineage="Ryan et al. (2015) parameters, OR-combined masks, Fielding TN",
    ),
    Preset(
        key="tpv1",
        label="tropical_pacific v1",
        toml_name="tropical_pacific_denoise.toml",
        container="local-raw-10oct-tpv1",
        lineage="TPOS-tuned v1",
    ),
    Preset(
        key="tpv3",
        label="tropical_pacific v3",
        toml_name="tropical_pacific_denoise_v3.toml",
        container="local-raw-10oct-tpv3",
        lineage="TPOS-tuned v3 (= v1 with v2's transient tightening)",
    ),
)

PRESETS_BY_KEY = {p.key: p for p in PRESETS}

#: The arm every difference figure is referenced against.
BASELINE_PRESET = "ryan-inspired"

#: Immutable shared input container (stage-4 Sv), never written to.
SOURCE_CONTAINER = "local-raw-10oct"

#: The single operational day under study.
EXPERIMENT_DAY = "2023-10-10"

#: Pulse categories expected for that day.
EXPECTED_CATEGORIES: tuple[str, ...] = ("long_pulse", "short_pulse")

#: Products every preset run must produce for every category before the next
#: preset is allowed to start (Phase 4.19 artifact matrix).
CORE_PRODUCTS: tuple[str, ...] = (
    "--denoised.zarr",
    "--pruned.zarr",
    "--mvbs.zarr",
    "--nasc.zarr",
)

#: Emitted only under ``--emit-denoise-diagnostics``.
DIAGNOSTIC_PRODUCTS: tuple[str, ...] = (
    "--denoise_stats.json",
    "--masks.zarr",
)

REQUIRED_PRODUCTS: tuple[str, ...] = CORE_PRODUCTS + DIAGNOSTIC_PRODUCTS


# ── Hashing / fingerprinting ───────────────────────────────────────────────

_CHUNK = 1 << 20


def hash_file(path: Path) -> str:
    """SHA256 of a single file."""
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        while chunk := fh.read(_CHUNK):
            h.update(chunk)
    return h.hexdigest()


def _iter_files(root: Path) -> Iterable[Path]:
    for p in sorted(root.rglob("*")):
        if p.is_file() and p.name != ".DS_Store":
            yield p


def fingerprint_tree(root: Path) -> dict:
    """Recursive content hash of a directory tree (e.g. a zarr store).

    The digest covers relative paths *and* file contents, so a renamed or
    removed chunk changes the fingerprint. ``.DS_Store`` is excluded because
    macOS rewrites it on directory browse without touching the data.
    """
    root = Path(root)
    if not root.exists():
        raise FileNotFoundError(root)

    h = hashlib.sha256()
    n_files = 0
    n_bytes = 0
    for p in _iter_files(root):
        rel = p.relative_to(root).as_posix()
        h.update(rel.encode("utf-8"))
        h.update(b"\0")
        h.update(hash_file(p).encode("ascii"))
        h.update(b"\0")
        n_files += 1
        n_bytes += p.stat().st_size

    return {
        "path": str(root),
        "sha256": h.hexdigest(),
        "n_files": n_files,
        "n_bytes": n_bytes,
    }


def describe_sv_zarr(path: Path) -> dict:
    """Structural + acquisition summary of a stage-4 Sv zarr."""
    import numpy as np
    import xarray as xr

    ds = xr.open_zarr(str(path))
    try:
        summary: dict[str, Any] = {
            "dims": {str(k): int(v) for k, v in ds.sizes.items()},
            "data_vars": sorted(str(v) for v in ds.data_vars),
            "coords": sorted(str(c) for c in ds.coords),
        }

        if "channel" in ds.coords:
            summary["channels"] = [str(c) for c in ds["channel"].values]
        if "frequency_nominal" in ds:
            fn = ds["frequency_nominal"]
            extra = [d for d in fn.dims if d != "channel"]
            if extra:
                fn = fn.isel({d: 0 for d in extra})
            summary["frequencies_hz"] = [
                float(v) for v in np.atleast_1d(fn.values)
            ]

        if "ping_time" in ds.coords:
            pt = ds["ping_time"].values
            summary["n_pings"] = int(pt.size)
            if pt.size:
                summary["acquisition_start_utc"] = str(np.datetime_as_string(pt.min(), unit="s"))
                summary["acquisition_end_utc"] = str(np.datetime_as_string(pt.max(), unit="s"))

        gps: dict[str, Any] = {}
        for var in ("latitude", "longitude"):
            if var in ds or var in ds.coords:
                da = ds[var]
                extra = [d for d in da.dims if d != "ping_time"]
                if extra:
                    da = da.mean(dim=extra, skipna=True)
                vals = np.asarray(da.values, dtype=float)
                finite = np.isfinite(vals)
                gps[var] = {
                    "n": int(vals.size),
                    "n_finite": int(finite.sum()),
                    "min": float(vals[finite].min()) if finite.any() else None,
                    "max": float(vals[finite].max()) if finite.any() else None,
                    "n_unique": int(np.unique(vals[finite]).size) if finite.any() else 0,
                }
        if gps:
            summary["gps"] = gps

        if "depth" in ds:
            d = ds["depth"]
            summary["depth_range_m"] = [
                float(d.min().values),
                float(d.max().values),
            ]
        return summary
    finally:
        ds.close()


def build_source_manifest(source_root: Path, day: str = EXPERIMENT_DAY) -> dict:
    """Fingerprint + describe both stage-4 Sv zarrs for *day*.

    This is the immutability gate: the exact same dict must be reproducible
    after every preset run.
    """
    day_dir = Path(source_root) / day
    sources: dict[str, Any] = {}
    for category in EXPECTED_CATEGORIES:
        zarr_path = day_dir / f"{day}--{category}.zarr"
        if not zarr_path.exists():
            raise FileNotFoundError(
                f"Missing stage-4 Sv zarr for {day}/{category}: {zarr_path}. "
                "The comparison requires both pulse categories to be present."
            )
        entry = fingerprint_tree(zarr_path)
        entry["description"] = describe_sv_zarr(zarr_path)
        sources[category] = entry

    return {
        "schema": EXPERIMENT_MANIFEST_SCHEMA,
        "created_utc": _utcnow(),
        "day": day,
        "source_root": str(Path(source_root).resolve()),
        "source_container": SOURCE_CONTAINER,
        "categories": list(EXPECTED_CATEGORIES),
        "sources": sources,
        "tolerances": TOLERANCES.to_dict(),
        "presets": [
            {
                "key": p.key,
                "label": p.label,
                "toml": p.toml_name,
                "toml_sha256": hash_file(p.toml_path) if p.toml_path.exists() else None,
                "container": p.container,
                "lineage": p.lineage,
            }
            for p in PRESETS
        ],
        "baseline_preset": BASELINE_PRESET,
        "code": capture_code_revision(),
        "environment": capture_environment(),
    }


def verify_source_manifest(manifest: dict, source_root: Path | None = None) -> list[str]:
    """Re-hash the sources and report any drift. Empty list = unchanged."""
    root = Path(source_root or manifest["source_root"])
    day = manifest["day"]
    problems: list[str] = []
    for category, recorded in manifest["sources"].items():
        zarr_path = root / day / f"{day}--{category}.zarr"
        if not zarr_path.exists():
            problems.append(f"{category}: source zarr disappeared ({zarr_path})")
            continue
        current = fingerprint_tree(zarr_path)
        if current["sha256"] != recorded["sha256"]:
            problems.append(
                f"{category}: source fingerprint changed "
                f"({recorded['sha256'][:12]} → {current['sha256'][:12]})"
            )
    return problems


# ── Provenance capture ─────────────────────────────────────────────────────


def _git(*args: str) -> str | None:
    try:
        out = subprocess.run(
            ["git", "-C", str(REPO_ROOT), *args],
            capture_output=True, text=True, timeout=30, check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    return out.stdout.strip() if out.returncode == 0 else None


def capture_code_revision() -> dict:
    """Git revision plus content hashes of every dirty tracked file.

    A dirty worktree is allowed (this is research code) but must be recorded
    precisely enough that the exact inputs can be reconstructed.
    """
    head = _git("rev-parse", "HEAD")
    branch = _git("rev-parse", "--abbrev-ref", "HEAD")
    porcelain = _git("status", "--porcelain") or ""

    dirty: dict[str, str | None] = {}
    for line in porcelain.splitlines():
        if len(line) < 4:
            continue
        rel = line[3:].strip().strip('"')
        if " -> " in rel:  # rename
            rel = rel.split(" -> ", 1)[1]
        path = REPO_ROOT / rel
        dirty[rel] = hash_file(path) if path.is_file() else None

    return {
        "repo_root": str(REPO_ROOT),
        "head": head,
        "branch": branch,
        "dirty": bool(dirty),
        "dirty_files": dict(sorted(dirty.items())),
    }


_TRACKED_PACKAGES = (
    "echopype", "xarray", "numpy", "dask", "zarr", "flox",
    "numba", "scipy", "pandas", "matplotlib",
)


def capture_environment() -> dict:
    """Interpreter + key package versions, plus a full ``pip freeze``."""
    from importlib import metadata

    versions: dict[str, str | None] = {}
    for name in _TRACKED_PACKAGES:
        try:
            versions[name] = metadata.version(name)
        except metadata.PackageNotFoundError:
            versions[name] = None

    try:
        freeze = subprocess.run(
            [sys.executable, "-m", "pip", "freeze"],
            capture_output=True, text=True, timeout=120, check=False,
        ).stdout.splitlines()
    except (OSError, subprocess.SubprocessError):
        freeze = []

    return {
        "python": sys.version.split()[0],
        "executable": sys.executable,
        "platform": platform.platform(),
        "packages": versions,
        "pip_freeze": freeze,
    }


# ── JSON helpers ───────────────────────────────────────────────────────────


class StrictJSONError(ValueError):
    """Raised when a value cannot be represented as strict JSON."""


def json_safe(value: Any) -> Any:
    """Convert *value* to strict JSON.

    ``NaN`` / ``Infinity`` are **not** valid JSON. Rather than emitting the
    ``NaN`` token that most parsers reject, non-finite floats become ``null``.
    Callers that need to distinguish "no data" from "computed as NaN" must
    carry an explicit status field alongside the value.
    """
    import numpy as np

    if isinstance(value, dict):
        return {str(k): json_safe(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [json_safe(v) for v in value]
    if isinstance(value, (np.bool_, bool)):
        return bool(value)
    if isinstance(value, (np.integer, int)):
        return int(value)
    if isinstance(value, (np.floating, float)):
        f = float(value)
        return f if math.isfinite(f) else None
    if isinstance(value, np.ndarray):
        return [json_safe(v) for v in value.tolist()]
    if isinstance(value, (np.datetime64,)):
        return str(np.datetime_as_string(value, unit="ns"))
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, Path):
        return str(value)
    if value is None or isinstance(value, str):
        return value
    return str(value)


def write_json_atomic(path: Path, payload: dict) -> Path:
    """Write JSON to *path* atomically (temp file + rename).

    A partially-written manifest is worse than a missing one: the validator
    would accept it. ``os.replace`` is atomic within a filesystem.
    """
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(json_safe(payload), indent=2, sort_keys=False, allow_nan=False)
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=path.name, suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(text)
            fh.write("\n")
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise
    return path


def read_json(path: Path, expected_schema: str | None = None) -> dict:
    """Read a JSON artifact and optionally assert its schema tag."""
    with open(path, encoding="utf-8") as fh:
        payload = json.load(fh)
    if expected_schema is not None:
        actual = payload.get("schema")
        if actual != expected_schema:
            raise StrictJSONError(
                f"{path}: schema {actual!r} != expected {expected_schema!r}"
            )
    return payload


def _utcnow() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


# ── Artifact matrix validation ─────────────────────────────────────────────


def validate_artifact_matrix(
    run_root: Path,
    day: str = EXPERIMENT_DAY,
    categories: Iterable[str] = EXPECTED_CATEGORIES,
    required: Iterable[str] = REQUIRED_PRODUCTS,
) -> list[str]:
    """Check that every (category × product) artifact exists under *run_root*.

    *run_root* is the container directory, e.g.
    ``.../local-raw-10oct-tpv3``. Returns a list of missing artifacts.
    """
    run_root = Path(run_root)
    day_dir = run_root / day
    missing: list[str] = []
    if not day_dir.is_dir():
        return [f"missing day directory {day_dir}"]
    for category in categories:
        for suffix in required:
            candidate = day_dir / f"{day}--{category}{suffix}"
            if not candidate.exists():
                missing.append(str(candidate.relative_to(run_root)))
    return missing


# ── CLI ────────────────────────────────────────────────────────────────────


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_fp = sub.add_parser("fingerprint", help="Build the experiment manifest")
    p_fp.add_argument("--source-root", required=True, type=Path)
    p_fp.add_argument("--day", default=EXPERIMENT_DAY)
    p_fp.add_argument("--out", required=True, type=Path)

    p_v = sub.add_parser("verify", help="Re-verify source fingerprints")
    p_v.add_argument("--manifest", required=True, type=Path)
    p_v.add_argument("--source-root", type=Path)

    p_a = sub.add_parser("validate-artifacts", help="Check a run's artifact matrix")
    p_a.add_argument("--run-root", required=True, type=Path)
    p_a.add_argument("--day", default=EXPERIMENT_DAY)
    p_a.add_argument(
        "--categories",
        default=",".join(EXPECTED_CATEGORIES),
        help="Comma-separated categories the run declared. A restricted run "
             "must be validated against its own declaration, not the default.",
    )

    args = parser.parse_args(argv)

    if args.cmd == "fingerprint":
        manifest = build_source_manifest(args.source_root, args.day)
        write_json_atomic(args.out, manifest)
        for cat, entry in manifest["sources"].items():
            desc = entry["description"]
            print(
                f"{cat}: sha256={entry['sha256'][:16]} "
                f"pings={desc.get('n_pings')} "
                f"{desc.get('acquisition_start_utc')} → {desc.get('acquisition_end_utc')}"
            )
        print(f"Wrote {args.out}")
        return 0

    if args.cmd == "verify":
        manifest = read_json(args.manifest, EXPERIMENT_MANIFEST_SCHEMA)
        problems = verify_source_manifest(manifest, args.source_root)
        if problems:
            for p in problems:
                print(f"DRIFT: {p}", file=sys.stderr)
            return 1
        print("Source fingerprints unchanged.")
        return 0

    if args.cmd == "validate-artifacts":
        categories = [c.strip() for c in args.categories.split(",") if c.strip()]
        missing = validate_artifact_matrix(args.run_root, args.day, categories)
        if missing:
            for m in missing:
                print(f"MISSING: {m}", file=sys.stderr)
            return 1
        print(f"Artifact matrix complete ({', '.join(categories)}).")
        return 0

    return 2


if __name__ == "__main__":
    raise SystemExit(main())
