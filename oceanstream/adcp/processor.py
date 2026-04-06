"""ADCP processing module for Acoustic Doppler Current Profiler data."""
from __future__ import annotations

import logging
from pathlib import Path
from time import perf_counter
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import pandas as pd
    from ..providers.base import ProviderBase

logger = logging.getLogger("oceanstream")


class AdcpProcessor:
    """Processor for ADCP data."""

    def __init__(self, provider: "ProviderBase", verbose: bool = False):
        self.provider = provider
        self.verbose = verbose
        self._start_time = perf_counter()

    def log(self, message: str) -> None:
        """Log a message if verbose is enabled."""
        if self.verbose:
            print(f"[adcp] {message}")

    def elapsed_time(self) -> float:
        """Get elapsed time since processor initialization."""
        return perf_counter() - self._start_time


def process(
    provider: "ProviderBase",
    input_dir: Path,
    output_dir: Path,
    verbose: bool = False,
    dry_run: bool = False,
    transducer_depth: float = 7.0,
    ensemble_interval: float = 120.0,
) -> None:
    """Process Acoustic Doppler Current Profiler (ADCP) data.

    Reads RDI binary ``.raw`` files, applies beam-to-earth coordinate
    transforms, averages into ensembles, and writes NetCDF output.

    Parameters
    ----------
    provider : ProviderBase
        Data provider instance.
    input_dir : Path
        Directory containing ``.raw`` RDI binary files.
    output_dir : Path
        Output directory for processed NetCDF files.
    verbose : bool
        Enable detailed progress information.
    dry_run : bool
        Analyze inputs without writing files.
    transducer_depth : float
        Transducer depth below surface in meters.
    ensemble_interval : float
        Averaging interval in seconds.
    """
    from .rdi_reader import scan_rdi_files

    processor = AdcpProcessor(provider, verbose=verbose)

    raw_files = scan_rdi_files(input_dir)

    if dry_run:
        print("[adcp] Dry Run Summary")
        print("----------------------")
        print(f"Source directory    : {input_dir}")
        print(f"Output (planned)   : {output_dir}")
        print(f"Provider           : {provider.name}")
        print(f"Raw files found    : {len(raw_files)}")
        for f in raw_files:
            print(f"  - {f.name} ({f.stat().st_size / 1024 / 1024:.1f} MB)")
        print(f"Transducer depth   : {transducer_depth} m")
        print(f"Ensemble interval  : {ensemble_interval} s")
        print("Pipeline           : RDI binary → beam→earth transform → ensemble average → NetCDF")
        return

    if not raw_files:
        print(f"[adcp] No .raw files found in {input_dir}")
        return

    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    from .rdi_reader import read_rdi
    from .transforms import beam_to_earth, ensemble_average

    for raw_file in raw_files:
        processor.log(f"Reading {raw_file.name} ...")
        raw_ds = read_rdi(raw_file)

        processor.log(
            f"  {raw_ds.sizes['time']} pings, "
            f"{raw_ds.sizes['range']} bins, "
            f"coord_sys={raw_ds.attrs.get('coord_sys', '?')}"
        )

        processor.log("  Transforming beam → earth ...")
        earth_ds = beam_to_earth(raw_ds, transducer_depth=transducer_depth)

        processor.log(f"  Averaging into {ensemble_interval}s ensembles ...")
        avg_ds = ensemble_average(earth_ds, interval_seconds=ensemble_interval)

        out_name = raw_file.stem + "_processed.nc"
        out_path = output_dir / out_name

        processor.log(f"  Writing {out_path} ...")
        avg_ds.to_netcdf(out_path)

        processor.log(
            f"  Done: {avg_ds.sizes['time']} ensembles, "
            f"elapsed {processor.elapsed_time():.1f}s"
        )

    print(
        f"[adcp] Processed {len(raw_files)} file(s) → {output_dir} "
        f"({processor.elapsed_time():.1f}s)"
    )


def process_file(
    raw_path: Path | str,
    transducer_depth: float = 7.0,
    ensemble_interval: float = 120.0,
    corr_threshold: int = 64,
) -> pd.DataFrame:
    """Process a single RDI ADCP ``.raw`` file into a flat DataFrame.

    Full pipeline: read → beam→earth → ensemble average → flatten.

    Parameters
    ----------
    raw_path : Path or str
        Path to the ``.raw`` binary file.
    transducer_depth : float
        Transducer depth below surface in metres.
    ensemble_interval : float
        Averaging interval in seconds.
    corr_threshold : int
        Minimum beam-averaged correlation (0–255).

    Returns
    -------
    pd.DataFrame
        One row per (ensemble, depth cell) with columns: ``time``,
        ``depth``, ``u``, ``v``, ``w``, ``amp``, ``heading``,
        ``temperature``, ``num_pings``.
    """
    from .rdi_reader import read_rdi
    from .transforms import adcp_to_dataframe, beam_to_earth, ensemble_average

    raw_path = Path(raw_path)
    raw_ds = read_rdi(raw_path)

    n_pings = raw_ds.sizes["time"]
    n_bins = raw_ds.sizes["range"]
    freq = raw_ds.attrs.get("freq", "?")
    logger.info(
        "ADCP: %s — %d pings, %d bins, %s kHz, coord_sys=%s",
        raw_path.name, n_pings, n_bins, freq,
        raw_ds.attrs.get("coord_sys", "?"),
    )

    earth_ds = beam_to_earth(
        raw_ds,
        transducer_depth=transducer_depth,
        corr_threshold=corr_threshold,
    )

    avg_ds = ensemble_average(earth_ds, interval_seconds=ensemble_interval)

    logger.info(
        "ADCP: %d ensembles at %ds interval",
        avg_ds.sizes["time"], int(ensemble_interval),
    )

    df = adcp_to_dataframe(avg_ds)
    logger.info(
        "ADCP flattened: %d ensembles × %d depth cells = %d rows",
        avg_ds.sizes["time"], avg_ds.sizes["depth_cell"], len(df),
    )
    return df
