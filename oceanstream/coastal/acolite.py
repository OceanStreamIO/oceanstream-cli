"""ACOLITE driver — atmospheric correction as a subprocess.

ACOLITE is a separate program with its own dependency tree, its own version
cadence and a GPL licence. It is deliberately *not* a declared dependency: it is
invoked through ``sys.executable`` against a user-supplied install directory,
and its absence produces an actionable error rather than an import failure at
package load.

Everything here is about getting a scene *into* the library. Once ACOLITE has
written its per-band surface-reflectance GeoTIFFs, :class:`~.scene.Scene` takes
over and nothing downstream knows or cares how the rasters were produced — which
is what lets the same physics run against rasters an external system corrected.
"""

from __future__ import annotations

import datetime as dt
import logging
import subprocess
import sys
from collections.abc import Mapping
from pathlib import Path

logger = logging.getLogger(__name__)

#: Files ACOLITE writes per band at L2R. The digit class matters: without it the
#: glob also matches the ``rgb_rhos`` quicklook composite, which has no numeric
#: suffix and would be parsed as a band with an unparseable wavelength.
L2R_RHOS_GLOB = "*_L2R_rhos_[0-9]*.tif"
L2W_RRS_GLOB = "*_L2W_Rrs_[0-9]*.tif"
SZA_GLOB = "*_L2R_sza.tif"

#: Wavelength windows a usable output must cover, in nm. Deliberately wide:
#: platforms in the same constellation place their band centres a few nm apart,
#: and a completeness check that only accepted one platform's exact centres
#: would reject a perfectly good scene.
_REQUIRED_WINDOWS = {
    "blue": (440, 500),
    "green": (540, 580),
    "red": (650, 680),
}


def acolite_settings(
    aoi_limit: tuple[float, float, float, float],
    input_path: Path,
    output_dir: Path,
    template: Path | None = None,
    extra: Mapping[str, str] | None = None,
) -> str:
    """Build an ACOLITE settings file body.

    ``aoi_limit`` must already be in ACOLITE's ``(south, west, north, east)``
    ordering — take it from :attr:`oceanstream.coastal.aoi.AOI.acolite_limit`
    rather than reordering by hand, because a transposed limit silently yields
    an empty subset rather than an error.

    A ``template`` is appended to rather than parsed, so an operator's tuned
    settings survive verbatim and the injected keys — which must win — come
    last.
    """
    lines = []
    if template is not None:
        template = Path(template)
        if not template.is_file():
            raise FileNotFoundError(
                f"ACOLITE settings template not found at {template}. Pass an existing "
                "file via --acolite-template, or omit it to run with ACOLITE's own "
                "defaults plus the injected inputfile/output/limit keys."
            )
        lines.append(template.read_text().rstrip())
        lines.append(
            f"# ---- injected by oceanstream.coastal at "
            f"{dt.datetime.now(dt.UTC).isoformat()} ----"
        )
    lines.append(f"inputfile={Path(input_path).resolve()}")
    lines.append(f"output={Path(output_dir).resolve()}")
    lines.append("limit={},{},{},{}".format(*aoi_limit))
    for key, value in (extra or {}).items():
        lines.append(f"{key}={value}")
    return "\n".join(lines) + "\n"


def write_acolite_settings(
    output_dir: Path,
    aoi_limit: tuple[float, float, float, float],
    input_path: Path,
    template: Path | None = None,
    extra: Mapping[str, str] | None = None,
) -> Path:
    """Write ``acolite_settings.txt`` into the run directory and return its path.

    The settings file lives beside the output rather than in a scratch
    directory, because it is the only complete record of how a given set of
    rasters was produced.
    """
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    settings_path = output_dir / "acolite_settings.txt"
    settings_path.write_text(
        acolite_settings(aoi_limit, input_path, output_dir, template=template, extra=extra)
    )
    return settings_path


def wavelength_of(path: Path) -> float:
    """Wavelength in nm from an ACOLITE per-band filename.

    ACOLITE encodes it as the final underscore-separated token, e.g.
    ``S2A_MSI_2026_06_27_L2R_rhos_561.tif`` → 561.0.
    """
    token = Path(path).stem.rsplit("_", 1)[-1]
    try:
        return float(token)
    except ValueError:
        raise ValueError(
            f"Cannot read a wavelength from {Path(path).name!r}: trailing token "
            f"{token!r} is not numeric. Expected an ACOLITE per-band file such as "
            "'..._L2R_rhos_561.tif'."
        ) from None


def _band_wavelengths(output_dir: Path) -> list[float]:
    """Wavelengths of every per-band raster in an ACOLITE output directory.

    Both product levels count. Which one is present depends on the operator's
    ``l2w_parameters`` setting, and a run configured to emit only L2W Rrs is
    just as complete as an L2R one — treating it as incomplete would re-run the
    scene on every retry forever.
    """
    output_dir = Path(output_dir)
    if not output_dir.is_dir():
        return []
    wavelengths = []
    for glob in (L2R_RHOS_GLOB, L2W_RRS_GLOB):
        for path in output_dir.glob(glob):
            try:
                wavelengths.append(wavelength_of(path))
            except ValueError:
                continue
    return wavelengths


def output_is_complete(output_dir: Path) -> bool:
    """Whether a directory holds a usable ACOLITE run.

    Used to skip re-running a scene. Checks band coverage rather than mere file
    presence, because ACOLITE can exit successfully having written only a
    partial band set when a tile clips the AOI.
    """
    wavelengths = _band_wavelengths(output_dir)
    if not wavelengths:
        return False
    return all(
        any(lo <= w <= hi for w in wavelengths) for lo, hi in _REQUIRED_WINDOWS.values()
    )


def missing_windows(output_dir: Path) -> list[str]:
    """Which spectral windows an incomplete run is missing, for the error path."""
    wavelengths = _band_wavelengths(output_dir)
    return [
        name
        for name, (lo, hi) in _REQUIRED_WINDOWS.items()
        if not any(lo <= w <= hi for w in wavelengths)
    ]


def run_acolite(
    settings_path: Path,
    acolite_path: Path,
    python_executable: str | None = None,
    timeout_s: float | None = None,
) -> None:
    """Run ACOLITE's CLI against a settings file.

    ``acolite_path`` is the checkout directory containing ``launch_acolite.py``.
    It is also the working directory, because ACOLITE resolves several of its
    auxiliary data paths relative to its own root.
    """
    settings_path = Path(settings_path)
    if not settings_path.is_file():
        raise FileNotFoundError(
            f"ACOLITE settings file not found at {settings_path}. Build one with "
            "oceanstream.coastal.acolite.write_acolite_settings()."
        )
    acolite_path = Path(acolite_path)
    launcher = acolite_path / "launch_acolite.py"
    if not launcher.exists():
        raise FileNotFoundError(
            f"ACOLITE launcher not found at {launcher}. ACOLITE is not a declared "
            "dependency — clone it from https://github.com/acolite/acolite and pass "
            "the checkout directory via --acolite-path or the ACOLITE_PATH "
            "environment variable."
        )
    cmd = [
        python_executable or sys.executable,
        str(launcher),
        "--cli",
        "--settings",
        str(settings_path.resolve()),
    ]
    logger.info("[acolite] $ %s", " ".join(cmd))
    subprocess.run(cmd, check=True, cwd=acolite_path, timeout=timeout_s)


def correct_scene(
    input_path: Path,
    output_dir: Path,
    aoi_limit: tuple[float, float, float, float],
    acolite_path: Path,
    template: Path | None = None,
    extra: Mapping[str, str] | None = None,
    force: bool = False,
) -> Path:
    """Atmospherically correct one L1 bundle. Returns the output directory.

    Skips the run when ``output_dir`` already holds a complete band set, unless
    ``force``. A re-run costs minutes per scene and the check is cheap, so the
    default is to trust existing output.
    """
    output_dir = Path(output_dir)
    if not force and output_is_complete(output_dir):
        logger.info("ACOLITE output already complete in %s — skipping", output_dir)
        return output_dir
    settings_path = write_acolite_settings(
        output_dir, aoi_limit, Path(input_path), template=template, extra=extra
    )
    run_acolite(settings_path, acolite_path)
    if not output_is_complete(output_dir):
        raise RuntimeError(
            f"ACOLITE exited successfully but {output_dir} is missing the "
            f"{', '.join(missing_windows(output_dir))} band(s). This usually means "
            "the AOI limit falls outside the tile footprint, or the input bundle "
            "is partially masked. Check acolite_settings.txt in that directory."
        )
    return output_dir
