"""Integration tests for R2R CTD processing.

Decoding a .hex file takes about 1.5 s per 1000 scans: seabirdscientific's
``read_hex_file`` fills its DataFrame one cell at a time. The tests that decode
a cast are marked ``slow``, which the default run leaves out; run them with
``pytest -m slow`` or ``make test-slow``.
"""

import tarfile
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

# Skip all tests if seabirdscientific not installed
pytest.importorskip("seabirdscientific")

PROJECT_ROOT = Path(__file__).resolve().parents[3]
CTD_ARCHIVE = PROJECT_ROOT / "raw_data" / "r2r" / "RR2402_160202_ctd.tar.gz"
# The shortest cast of RR2402 (8,105 scans); the archive holds 109 casts, 349 MB
CTD_CAST = "RR2402_cast86"

VALIDATION_DIR = PROJECT_ROOT / "raw_data" / "r2r" / "RR2205_ctd_validation"
VALIDATION_RAW = VALIDATION_DIR / "raw"
VALIDATION_CNV = VALIDATION_DIR / "processed" / "11901_1db.cnv"


class TestR2RCTDProcessing:
    """Tests for SeaBird CTD processing from R2R archives."""

    @pytest.fixture(scope="class")
    def ctd_data_dir(self, tmp_path_factory: pytest.TempPathFactory) -> Path:
        """Extract one cast's files (hex, hdr, bl, XMLCON) from the R2R archive in raw_data/r2r/."""
        if not CTD_ARCHIVE.exists():
            pytest.skip(
                f"R2R CTD archive not found: {CTD_ARCHIVE.relative_to(PROJECT_ROOT)}  "
                "(run: python scripts/fetch_test_data.py --category r2r)"
            )

        out_dir = tmp_path_factory.mktemp("r2r_ctd")
        with tarfile.open(CTD_ARCHIVE, "r:gz") as tf:
            members = [m for m in tf.getmembers() if Path(m.name).stem.lower() == CTD_CAST.lower()]
            tf.extractall(path=out_dir, members=members, filter="data")

        data_dir = out_dir / "RR2402" / "160202" / "data"
        assert data_dir.exists(), f"Expected data dir not found after extraction: {data_dir}"
        return data_dir

    @pytest.fixture(scope="class")
    def processed_cast(
        self, ctd_data_dir: Path, tmp_path_factory: pytest.TempPathFactory
    ) -> tuple[pd.DataFrame, Path]:
        """The cast decoded once, with its CSV written: (DataFrame, CSV directory)."""
        from oceanstream.sensors.processors.sbe911 import find_cast_files, process_ctd_cast

        output_dir = tmp_path_factory.mktemp("ctd_output")
        df = process_ctd_cast(find_cast_files(ctd_data_dir)[0], output_dir=output_dir)
        assert df is not None
        return df, output_dir

    def test_find_cast_files(self, ctd_data_dir: Path):
        """Test finding CTD cast files in directory."""
        from oceanstream.sensors.processors.sbe911 import find_cast_files

        casts = find_cast_files(ctd_data_dir)

        assert len(casts) == 1
        cast = casts[0]
        assert cast.cast_id == "cast86"
        assert cast.cruise_id == "RR2402"
        assert cast.hex_file.exists()
        assert cast.hex_file.suffix == '.hex'
        # The XMLCON is named RR2402_CAST86.XMLCON: matched regardless of case
        assert cast.hdr_file is not None
        assert cast.xmlcon_file is not None

    def test_parse_hdr_file(self, ctd_data_dir: Path):
        """Test parsing CTD header file."""
        from oceanstream.sensors.processors.sbe911 import find_cast_files, parse_hdr_file

        casts = find_cast_files(ctd_data_dir)

        # Find a cast with header file
        cast_with_hdr = next((c for c in casts if c.hdr_file), None)
        if cast_with_hdr is None:
            pytest.skip("No cast with header file found")

        hdr = parse_hdr_file(cast_with_hdr.hdr_file)

        # Should extract position
        assert 'latitude' in hdr
        assert 'longitude' in hdr
        assert -90 <= hdr['latitude'] <= 90
        assert -180 <= hdr['longitude'] <= 180

        # Should extract time
        assert 'start_time' in hdr
        assert hdr['start_time'] is not None

    def test_parse_xmlcon_file(self, ctd_data_dir: Path):
        """Test parsing CTD configuration file."""
        from oceanstream.sensors.processors.sbe911 import find_cast_files, parse_xmlcon_file

        casts = find_cast_files(ctd_data_dir)

        # Find a cast with XMLCON file
        cast_with_xmlcon = next((c for c in casts if c.xmlcon_file), None)
        if cast_with_xmlcon is None:
            pytest.skip("No cast with XMLCON file found")

        config = parse_xmlcon_file(cast_with_xmlcon.xmlcon_file)

        # Should have sensor configuration
        assert 'sensors' in config
        assert len(config['sensors']) > 0

        # First sensor should be temperature
        assert config['sensors'][0]['index'] == 0

    @pytest.mark.slow
    def test_process_ctd_cast(self, processed_cast: tuple[pd.DataFrame, Path]):
        """Test processing a CTD cast to DataFrame."""
        df = processed_cast[0]

        assert len(df) > 0

        # Should have essential columns
        assert 'cast_id' in df.columns
        assert 'cruise_id' in df.columns
        assert 'scan' in df.columns

        # Should have raw data columns
        assert 'temperature_freq' in df.columns
        assert 'conductivity_freq' in df.columns
        assert 'pressure_freq' in df.columns

    @pytest.mark.slow
    def test_process_ctd_cast_output_csv(self, processed_cast: tuple[pd.DataFrame, Path]):
        """Test writing processed CTD data to CSV."""
        df, output_dir = processed_cast

        # Check CSV was written
        csv_files = list(output_dir.glob("*.csv"))
        assert len(csv_files) == 1

        # Read CSV and verify
        df_read = pd.read_csv(csv_files[0])
        assert len(df_read) == len(df)

    def test_ctd_descriptor_processor(self, ctd_data_dir: Path):
        """Test CTD sensor descriptor creation."""
        from oceanstream.providers.r2r.r2r_metadata import R2RFileInfo, R2RSensorInfo
        from oceanstream.sensors.processors.sbe911 import ctd_descriptor_processor

        file_info = R2RFileInfo(
            campaign_id="RR2402",
            platform="R/V Roger Revelle",
        )
        sensor_info = R2RSensorInfo(
            sensor_type="ctd",
            description="SeaBird SBE-911+",
        )

        descriptor = ctd_descriptor_processor(
            ctd_data_dir,
            file_info,
            sensor_info,
            "r2r",
        )

        assert descriptor.sensor_type == "ctd"
        assert descriptor.sensor_id == "sbe-911plus"
        assert descriptor.campaign_id == "RR2402"
        assert "cast_count" in descriptor.metadata


class TestCTDCalibratedConversion:
    """Validate calibrated CTD conversion against NCEI-processed CNV data.

    Uses cast 11901 from cruise RR2205 (NCEI archive 0280096).
    Raw hex + XMLCON → calibrated T/C/P/S/depth, compared against
    the Sea-Bird-processed 1-dbar binned CNV ground truth.
    """

    @pytest.fixture(scope="class")
    def validation_data(self) -> tuple[Path, pd.DataFrame]:
        """Return (raw_dir, ground_truth_df) for cast 11901."""
        if not VALIDATION_RAW.exists() or not VALIDATION_CNV.exists():
            pytest.skip(
                "Validation data not found: raw_data/r2r/RR2205_ctd_validation/ "
                "(run: python scripts/fetch_test_data.py --category r2r)"
            )
        from oceanstream.sensors.processors.sbe911 import parse_cnv_file

        cnv_df = parse_cnv_file(VALIDATION_CNV)
        return VALIDATION_RAW, cnv_df

    @pytest.fixture(scope="class")
    def calibrated_df(self, validation_data: tuple[Path, pd.DataFrame]) -> pd.DataFrame:
        """Process cast 11901 with calibrated conversion, once for the class."""
        raw_dir = validation_data[0]
        from oceanstream.sensors.processors.sbe911 import CTDCast, process_ctd_cast

        cast = CTDCast(
            cast_id="11901",
            cruise_id="RR2205",
            hex_file=raw_dir / "11901.hex",
            hdr_file=raw_dir / "11901.hdr",
            xmlcon_file=raw_dir / "11901.XMLCON",
        )
        df = process_ctd_cast(cast, calibrate=True)
        assert df is not None
        return df

    @staticmethod
    def _bin_average(df: pd.DataFrame, column: str, pressure_col: str = "prDM") -> pd.Series:
        """Bin-average *column* in 1-dbar bins matching CNV ``binavg``.

        Uses ``round()`` to assign scans to integer-dbar bins (matching
        the CNV 1-dbar interval).  Keeps all scans up to the maximum
        pressure (no upcast filtering — we tolerate small errors from
        the soak/upcast mixing and test only stable depth ranges).
        """
        df = df.copy()
        df["_bin"] = np.round(df[pressure_col]).astype(int)
        # Drop bins at 0 dbar (surface / in-air)
        df = df[df["_bin"] >= 1]
        return df.groupby("_bin")[column].mean()

    @staticmethod
    def _cnv_bins(cnv: pd.DataFrame, column: str) -> pd.Series:
        """Index CNV data by integer-dbar bins."""
        binned = cnv.copy()
        binned["_bin"] = np.round(binned["prDM"]).astype(int)
        return binned.set_index("_bin")[column]

    # ------------------------------------------------------------------
    # Structural tests
    # ------------------------------------------------------------------

    @pytest.mark.integration
    @pytest.mark.slow
    def test_calibrated_columns_present(self, calibrated_df: pd.DataFrame):
        """Calibrated output should contain T, C, P, S, depth columns."""
        for col in ("t090C", "c0S/m", "sal00", "prDM", "depSM"):
            assert col in calibrated_df.columns, f"Missing calibrated column: {col}"

    @pytest.mark.integration
    @pytest.mark.slow
    def test_secondary_channels_present(self, calibrated_df: pd.DataFrame):
        """Secondary T & C should also be calibrated."""
        for col in ("t190C", "c1S/m", "sal11"):
            assert col in calibrated_df.columns, f"Missing secondary column: {col}"

    @pytest.mark.integration
    @pytest.mark.slow
    def test_raw_columns_still_present(self, calibrated_df: pd.DataFrame):
        """Raw frequency columns must remain alongside calibrated data."""
        for col in ("temperature_freq", "conductivity_freq", "pressure_freq"):
            assert col in calibrated_df.columns, f"Missing raw column: {col}"

    # ------------------------------------------------------------------
    # Accuracy tests — compare bin-averaged values to CNV ground truth
    # ------------------------------------------------------------------

    @pytest.mark.integration
    @pytest.mark.slow
    def test_temperature_accuracy(
        self,
        calibrated_df: pd.DataFrame,
        validation_data: tuple[Path, pd.DataFrame],
    ):
        """Primary temperature should match CNV within ±0.05 °C.

        Only stable-depth bins (>= 23 dbar, below the thermocline) are
        compared, because the shallow soak/oscillation bins include
        scan-selection artifacts from SBE post-processing we don't replicate.
        """
        cnv = validation_data[1]
        our_binned = self._bin_average(calibrated_df, "t090C")
        cnv_bins = self._cnv_bins(cnv, "t090C")
        # Stable bins: below thermocline (>= 23 dbar)
        common = our_binned.index.intersection(cnv_bins.index)
        common = common[common >= 23]
        assert len(common) >= 5, "Not enough common stable-depth bins"
        diff = (our_binned.loc[common] - cnv_bins.loc[common]).abs()
        assert diff.max() < 0.05, f"Max temperature error {diff.max():.6f} °C exceeds 0.05"

    @pytest.mark.integration
    @pytest.mark.slow
    def test_conductivity_accuracy(
        self,
        calibrated_df: pd.DataFrame,
        validation_data: tuple[Path, pd.DataFrame],
    ):
        """Primary conductivity should match CNV within ±0.005 S/m."""
        cnv = validation_data[1]
        our_binned = self._bin_average(calibrated_df, "c0S/m")
        cnv_bins = self._cnv_bins(cnv, "c0S/m")
        common = our_binned.index.intersection(cnv_bins.index)
        common = common[common >= 23]
        assert len(common) >= 5
        diff = (our_binned.loc[common] - cnv_bins.loc[common]).abs()
        assert diff.max() < 0.005, f"Max conductivity error {diff.max():.6f} S/m exceeds 0.005"

    @pytest.mark.integration
    @pytest.mark.slow
    def test_pressure_accuracy(
        self,
        calibrated_df: pd.DataFrame,
        validation_data: tuple[Path, pd.DataFrame],
    ):
        """Pressure should match CNV within ±0.5 dbar per bin."""
        cnv = validation_data[1]
        our_binned = self._bin_average(calibrated_df, "prDM")
        cnv_bins = self._cnv_bins(cnv, "prDM")
        common = our_binned.index.intersection(cnv_bins.index)
        common = common[common >= 5]
        assert len(common) >= 10
        diff = (our_binned.loc[common] - cnv_bins.loc[common]).abs()
        assert diff.max() < 0.5, f"Max pressure error {diff.max():.3f} dbar exceeds 0.5"

    @pytest.mark.integration
    @pytest.mark.slow
    def test_salinity_accuracy(
        self,
        calibrated_df: pd.DataFrame,
        validation_data: tuple[Path, pd.DataFrame],
    ):
        """Practical salinity should match CNV within ±0.05 PSU."""
        cnv = validation_data[1]
        our_binned = self._bin_average(calibrated_df, "sal00")
        cnv_bins = self._cnv_bins(cnv, "sal00")
        common = our_binned.index.intersection(cnv_bins.index)
        common = common[common >= 23]
        assert len(common) >= 5
        diff = (our_binned.loc[common] - cnv_bins.loc[common]).abs()
        assert diff.max() < 0.05, f"Max salinity error {diff.max():.6f} PSU exceeds 0.05"

    @pytest.mark.integration
    @pytest.mark.slow
    def test_depth_accuracy(
        self,
        calibrated_df: pd.DataFrame,
        validation_data: tuple[Path, pd.DataFrame],
    ):
        """Depth should match CNV within ±0.5 m."""
        cnv = validation_data[1]
        our_binned = self._bin_average(calibrated_df, "depSM")
        cnv_bins = self._cnv_bins(cnv, "depSM")
        common = our_binned.index.intersection(cnv_bins.index)
        common = common[common >= 5]
        assert len(common) >= 10
        diff = (our_binned.loc[common] - cnv_bins.loc[common]).abs()
        assert diff.max() < 0.5, f"Max depth error {diff.max():.3f} m exceeds 0.5"

    # ------------------------------------------------------------------
    # CNV parser test
    # ------------------------------------------------------------------

    @pytest.mark.integration
    def test_parse_cnv_file(self, validation_data: tuple[Path, pd.DataFrame]):
        """CNV parser should produce correct shape and column names."""
        cnv = validation_data[1]
        assert len(cnv) == 28, f"Expected 28 rows, got {len(cnv)}"
        assert "t090C" in cnv.columns
        assert "prDM" in cnv.columns
        assert "sal00" in cnv.columns
        # First pressure should be around 1 dbar
        assert 0.5 < cnv["prDM"].iloc[0] < 2.0
