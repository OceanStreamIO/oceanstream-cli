"""Integration tests for new provider data processing through geotrack pipeline.

Tests each new provider (CMEMS, EMSO, EMODnet, NorSOOP, OceanLab, OOI,
PLOCAN, Generic) with sample data files in raw_data/.

The conftest.py ``isolated_metadata`` autouse fixture handles
Settings.METADATA_DIR isolation for all integration tests.
"""

from __future__ import annotations

import shutil
from pathlib import Path

import pandas as pd
import pyarrow.parquet as pq
import pytest

from oceanstream.geotrack.processor import convert
from oceanstream.providers import detect_provider, get_provider

PROJECT_ROOT = Path(__file__).resolve().parents[3]
RAW_DATA = PROJECT_ROOT / "raw_data"


def _copy_sample(src: Path, tmp_path: Path) -> Path:
    """Copy a sample CSV into an isolated tmp input directory."""
    if not src.exists():
        pytest.skip(f"Sample data not available: {src}")
    dest_dir = tmp_path / "in"
    dest_dir.mkdir(exist_ok=True)
    dest = dest_dir / src.name
    shutil.copy(src, dest)
    return dest


# ---------------------------------------------------------------------------
# EMSO
# ---------------------------------------------------------------------------
class TestEmsoIntegration:
    """EMSO OBSEA CTD data → GeoParquet."""

    @pytest.fixture
    def emso_file(self, tmp_path: Path) -> Path:
        return _copy_sample(RAW_DATA / "emso" / "EMSO_OBSEA_CTD_30min.csv", tmp_path)

    @pytest.mark.integration
    def test_emso_provider_detection(self, emso_file: Path) -> None:
        provider = detect_provider(emso_file)
        assert provider.name == "emso"

    @pytest.mark.integration
    def test_emso_convert(self, emso_file: Path, tmp_path: Path) -> None:
        out = tmp_path / "out"
        provider = get_provider("emso")

        convert(
            provider=provider,
            input_source=emso_file,
            output_dir=out,
            verbose=True,
            yes=True,
            force_reprocess=True,
        )

        parquets = list(out.rglob("*.parquet"))
        assert parquets, "No parquet files produced for EMSO data"

        df = pd.read_parquet(parquets[0])
        assert "latitude" in df.columns
        assert "longitude" in df.columns
        assert "time" in df.columns
        assert len(df) > 0

    @pytest.mark.integration
    def test_emso_metadata(self, emso_file: Path, tmp_path: Path) -> None:
        out = tmp_path / "out"
        provider = get_provider("emso")
        convert(
            provider=provider,
            input_source=emso_file,
            output_dir=out,
            verbose=True,
            yes=True,
            force_reprocess=True,
        )

        pq_file = next(out.rglob("*.parquet"))
        meta = pq.read_metadata(pq_file).metadata
        assert b"oceanstream:provider" in meta


# ---------------------------------------------------------------------------
# EMODnet Physics
# ---------------------------------------------------------------------------
class TestEmodnetIntegration:
    """EMODnet Physics BDC summary data → GeoParquet."""

    @pytest.fixture
    def emodnet_file(self, tmp_path: Path) -> Path:
        return _copy_sample(RAW_DATA / "emodnet" / "emodnet_physics_sample.csv", tmp_path)

    @pytest.mark.integration
    def test_emodnet_convert(self, emodnet_file: Path, tmp_path: Path) -> None:
        out = tmp_path / "out"
        provider = get_provider("emodnet")

        convert(
            provider=provider,
            input_source=emodnet_file,
            output_dir=out,
            verbose=True,
            yes=True,
            force_reprocess=True,
        )

        parquets = list(out.rglob("*.parquet"))
        assert parquets, "No parquet files produced for EMODnet data"

        df = pd.read_parquet(parquets[0])
        assert "latitude" in df.columns
        assert "longitude" in df.columns
        assert len(df) > 0

    @pytest.mark.integration
    def test_emodnet_metadata(self, emodnet_file: Path, tmp_path: Path) -> None:
        out = tmp_path / "out"
        provider = get_provider("emodnet")
        convert(
            provider=provider,
            input_source=emodnet_file,
            output_dir=out,
            verbose=True,
            yes=True,
            force_reprocess=True,
        )

        pq_file = next(out.rglob("*.parquet"))
        meta = pq.read_metadata(pq_file).metadata
        assert b"oceanstream:provider" in meta


# ---------------------------------------------------------------------------
# NorSOOP FerryBox
# ---------------------------------------------------------------------------
class TestNorsoopIntegration:
    """NorSOOP Color Fantasy FerryBox data (real, NIVA THREDDS) → GeoParquet."""

    @pytest.fixture
    def norsoop_file(self, tmp_path: Path) -> Path:
        return _copy_sample(
            RAW_DATA / "norsoop" / "color_fantasy_norsoop_2017.csv", tmp_path
        )

    @pytest.mark.integration
    def test_norsoop_provider_detection(self, norsoop_file: Path) -> None:
        provider = detect_provider(norsoop_file)
        assert provider.name == "norsoop"

    @pytest.mark.integration
    def test_norsoop_convert(self, norsoop_file: Path, tmp_path: Path) -> None:
        out = tmp_path / "out"
        provider = get_provider("norsoop")

        convert(
            provider=provider,
            input_source=norsoop_file,
            output_dir=out,
            verbose=True,
            yes=True,
            force_reprocess=True,
        )

        parquets = list(out.rglob("*.parquet"))
        assert parquets, "No parquet files produced for NorSOOP data"

        df = pd.read_parquet(parquets[0])
        assert "latitude" in df.columns
        assert "longitude" in df.columns
        assert len(df) == 50

    @pytest.mark.integration
    def test_norsoop_identify_platform(self) -> None:
        provider = get_provider("norsoop")
        platform = provider.identify_platform("color_fantasy_norsoop_2017.csv")
        assert platform == "color_fantasy"


# ---------------------------------------------------------------------------
# OceanLab
# ---------------------------------------------------------------------------

_OCEANLAB_INLINE_CSV = (
    "time,latitude,longitude,temperature,salinity,depth,platform_id\n"
    "2023-06-10T10:00:00Z,63.4571,10.3839,10.5,33.2,1.0,Munkholmen\n"
    "2023-06-10T10:15:00Z,63.4571,10.3839,10.6,33.3,1.0,Munkholmen\n"
    "2023-06-10T10:30:00Z,63.4571,10.3839,10.7,33.1,1.0,Munkholmen\n"
    "2023-06-10T10:45:00Z,63.4571,10.3839,10.8,33.4,1.0,Munkholmen\n"
    "2023-06-10T11:00:00Z,63.4571,10.3839,10.9,33.2,1.0,Munkholmen\n"
)


class TestOceanlabIntegration:
    """OceanLab Munkholmen buoy data → GeoParquet."""

    @pytest.fixture
    def oceanlab_file(self, tmp_path: Path) -> Path:
        dest_dir = tmp_path / "in"
        dest_dir.mkdir(exist_ok=True)
        f = dest_dir / "munkholmen_oceanlab_2023.csv"
        f.write_text(_OCEANLAB_INLINE_CSV)
        return f

    @pytest.mark.integration
    def test_oceanlab_provider_detection(self, oceanlab_file: Path) -> None:
        provider = detect_provider(oceanlab_file)
        assert provider.name == "oceanlab"

    @pytest.mark.integration
    def test_oceanlab_convert(self, oceanlab_file: Path, tmp_path: Path) -> None:
        out = tmp_path / "out"
        provider = get_provider("oceanlab")

        convert(
            provider=provider,
            input_source=oceanlab_file,
            output_dir=out,
            verbose=True,
            yes=True,
            force_reprocess=True,
        )

        parquets = list(out.rglob("*.parquet"))
        assert parquets, "No parquet files produced for OceanLab data"

        df = pd.read_parquet(parquets[0])
        assert "latitude" in df.columns
        assert "longitude" in df.columns
        assert len(df) > 0

    @pytest.mark.integration
    def test_oceanlab_stationary_constant_coordinates(
        self, oceanlab_file: Path, tmp_path: Path
    ) -> None:
        """OceanLab is stationary — all lat/lon should be identical."""
        out = tmp_path / "out"
        provider = get_provider("oceanlab")
        convert(
            provider=provider,
            input_source=oceanlab_file,
            output_dir=out,
            verbose=True,
            yes=True,
            force_reprocess=True,
        )

        df = pd.read_parquet(next(out.rglob("*.parquet")))
        assert df["latitude"].nunique() == 1
        assert df["longitude"].nunique() == 1

    @pytest.mark.integration
    def test_oceanlab_identify_platform(self) -> None:
        provider = get_provider("oceanlab")
        platform = provider.identify_platform("munkholmen_oceanlab_2023.csv")
        assert platform == "Munkholmen"


# ---------------------------------------------------------------------------
# OOI
# ---------------------------------------------------------------------------
class TestOoiIntegration:
    """OOI Coastal Endurance CE01ISSM data (real, OOI ERDDAP) → GeoParquet."""

    @pytest.fixture
    def ooi_file(self, tmp_path: Path) -> Path:
        return _copy_sample(
            RAW_DATA / "ooi" / "ooi_ce01issm_ctd_2023.csv", tmp_path
        )

    @pytest.mark.integration
    def test_ooi_provider_detection(self, ooi_file: Path) -> None:
        provider = detect_provider(ooi_file)
        assert provider.name == "ooi"

    @pytest.mark.integration
    def test_ooi_convert(self, ooi_file: Path, tmp_path: Path) -> None:
        out = tmp_path / "out"
        provider = get_provider("ooi")

        convert(
            provider=provider,
            input_source=ooi_file,
            output_dir=out,
            verbose=True,
            yes=True,
            force_reprocess=True,
        )

        parquets = list(out.rglob("*.parquet"))
        assert parquets, "No parquet files produced for OOI data"

        df = pd.read_parquet(parquets[0])
        assert "latitude" in df.columns
        assert "longitude" in df.columns
        assert len(df) == 50

    @pytest.mark.integration
    def test_ooi_identify_platform(self) -> None:
        provider = get_provider("ooi")
        platform = provider.identify_platform("ooi_ce01issm_ctd_2023.csv")
        assert platform == "CE01ISSM"


# ---------------------------------------------------------------------------
# PLOCAN
# ---------------------------------------------------------------------------
class TestPlocanIntegration:
    """PLOCAN Taliarte DOXY data (real, PLOCAN THREDDS) → GeoParquet."""

    @pytest.fixture
    def plocan_file(self, tmp_path: Path) -> Path:
        return _copy_sample(
            RAW_DATA / "plocan" / "plocan_taliarte_doxy_2019.csv", tmp_path
        )

    @pytest.mark.integration
    def test_plocan_provider_detection(self, plocan_file: Path) -> None:
        provider = detect_provider(plocan_file)
        assert provider.name == "plocan"

    @pytest.mark.integration
    def test_plocan_convert(self, plocan_file: Path, tmp_path: Path) -> None:
        out = tmp_path / "out"
        provider = get_provider("plocan")

        convert(
            provider=provider,
            input_source=plocan_file,
            output_dir=out,
            verbose=True,
            yes=True,
            force_reprocess=True,
        )

        parquets = list(out.rglob("*.parquet"))
        assert parquets, "No parquet files produced for PLOCAN data"

        df = pd.read_parquet(parquets[0])
        assert "latitude" in df.columns
        assert "longitude" in df.columns
        assert len(df) == 50


# ---------------------------------------------------------------------------
# Generic provider (auto-detect fallback)
# ---------------------------------------------------------------------------

_GENERIC_INLINE_CSV = (
    "timestamp,lat,lon,depth_m,temp_c,sal_psu\n"
    "2023-07-01T08:00:00Z,45.123,-12.456,5.0,18.2,35.1\n"
    "2023-07-01T08:15:00Z,45.124,-12.455,5.0,18.3,35.0\n"
    "2023-07-01T08:30:00Z,45.125,-12.454,5.0,18.4,35.2\n"
    "2023-07-01T08:45:00Z,45.126,-12.453,5.0,18.5,35.1\n"
    "2023-07-01T09:00:00Z,45.127,-12.452,5.0,18.6,35.3\n"
)


class TestGenericIntegration:
    """Generic CSV with non-standard column names → GeoParquet."""

    @pytest.fixture
    def generic_file(self, tmp_path: Path) -> Path:
        dest_dir = tmp_path / "in"
        dest_dir.mkdir(exist_ok=True)
        f = dest_dir / "ocean_survey_2023.csv"
        f.write_text(_GENERIC_INLINE_CSV)
        return f

    @pytest.mark.integration
    def test_generic_auto_detection_fallback(self, generic_file: Path) -> None:
        """Files that don't match any specific provider should fall back to generic."""
        provider = detect_provider(generic_file)
        assert provider.name == "generic"

    @pytest.mark.integration
    def test_generic_convert(self, generic_file: Path, tmp_path: Path) -> None:
        out = tmp_path / "out"
        provider = get_provider("generic")

        convert(
            provider=provider,
            input_source=generic_file,
            output_dir=out,
            verbose=True,
            yes=True,
            force_reprocess=True,
        )

        parquets = list(out.rglob("*.parquet"))
        assert parquets, "No parquet files produced for generic data"

        df = pd.read_parquet(parquets[0])
        assert "latitude" in df.columns
        assert "longitude" in df.columns
        assert len(df) > 0

    @pytest.mark.integration
    def test_generic_renames_nonstandard_columns(
        self, generic_file: Path, tmp_path: Path
    ) -> None:
        """Generic provider should rename 'lat'→'latitude', 'lon'→'longitude', etc."""
        out = tmp_path / "out"
        provider = get_provider("generic")
        convert(
            provider=provider,
            input_source=generic_file,
            output_dir=out,
            verbose=True,
            yes=True,
            force_reprocess=True,
        )

        df = pd.read_parquet(next(out.rglob("*.parquet")))
        assert "lat" not in df.columns
        assert "lon" not in df.columns


# ---------------------------------------------------------------------------
# Cross-provider: CLI auto-detect
# ---------------------------------------------------------------------------
class TestCLIAutoDetect:
    """Test CLI geotrack convert with auto-detect (no --provider flag)."""

    @pytest.fixture
    def norsoop_dir(self, tmp_path: Path) -> Path:
        src = RAW_DATA / "norsoop" / "color_fantasy_norsoop_2017.csv"
        if not src.exists():
            pytest.skip("NorSOOP sample data not available")
        dest_dir = tmp_path / "in"
        dest_dir.mkdir()
        shutil.copy(src, dest_dir / src.name)
        return dest_dir

    @pytest.mark.integration
    def test_cli_auto_detect_norsoop(
        self, norsoop_dir: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("PYTHONUTF8", "1")
        monkeypatch.setenv("PYTHONWARNINGS", "ignore")
        monkeypatch.setenv("NO_COLOR", "1")

        from typer.testing import CliRunner

        from oceanstream import cli as cli_module

        runner = CliRunner()
        out = tmp_path / "out"
        result = runner.invoke(
            cli_module.app,
            [
                "process",
                "geotrack",
                "convert",
                "--input-source",
                str(norsoop_dir),
                "--output-dir",
                str(out),
                "--force-reprocess",
                "--yes",
                "-v",
            ],
        )
        assert result.exit_code == 0, f"CLI failed:\n{result.output}"
        parquets = list(out.rglob("*.parquet"))
        assert parquets, f"No parquet files produced. CLI output:\n{result.output}"


# ---------------------------------------------------------------------------
# CMEMS In Situ TAC
# ---------------------------------------------------------------------------
class TestCmemsIntegration:
    """CMEMS in-situ data (NWS, Mediterranean, Baltic) → GeoParquet."""

    # -- NWS North Sea --

    @pytest.fixture
    def cmems_nws_file(self, tmp_path: Path) -> Path:
        return _copy_sample(
            RAW_DATA / "cmems" / "cmems_nws_northsea_insitu.csv", tmp_path
        )

    @pytest.mark.integration
    def test_cmems_provider_detection(self, cmems_nws_file: Path) -> None:
        provider = detect_provider(cmems_nws_file)
        assert provider.name == "cmems"

    @pytest.mark.integration
    def test_cmems_nws_convert(self, cmems_nws_file: Path, tmp_path: Path) -> None:
        out = tmp_path / "out"
        provider = get_provider("cmems")

        convert(
            provider=provider,
            input_source=cmems_nws_file,
            output_dir=out,
            verbose=True,
            yes=True,
            force_reprocess=True,
        )

        parquets = list(out.rglob("*.parquet"))
        assert parquets, "No parquet files produced for CMEMS NWS data"

        df = pd.read_parquet(parquets[0])
        assert "latitude" in df.columns
        assert "longitude" in df.columns
        assert "time" in df.columns
        assert len(df) > 0

    @pytest.mark.integration
    def test_cmems_enrichment_pivots_long_to_wide(
        self, cmems_nws_file: Path
    ) -> None:
        provider = get_provider("cmems")
        raw = pd.read_csv(cmems_nws_file)
        assert "variable" in raw.columns, "Input should be in long format"

        enriched = provider.enrich_dataframe(raw)
        assert "variable" not in enriched.columns, "Should be pivoted to wide"
        # PSAL → salinity after enrichment
        assert "salinity" in enriched.columns or "temperature" in enriched.columns

    @pytest.mark.integration
    def test_cmems_platform_types_decoded(self, cmems_nws_file: Path) -> None:
        provider = get_provider("cmems")
        raw = pd.read_csv(cmems_nws_file)
        enriched = provider.enrich_dataframe(raw)
        if "platform_type" in enriched.columns:
            types = enriched["platform_type"].unique()
            # Should be human-readable, not codes like MO/FB/TS
            assert all(len(str(t)) > 2 for t in types if pd.notna(t))

    # -- Mediterranean --

    @pytest.mark.integration
    def test_cmems_med_convert(self, tmp_path: Path) -> None:
        med_file = _copy_sample(
            RAW_DATA / "cmems" / "cmems_med_insitu.csv", tmp_path
        )
        out = tmp_path / "out"
        provider = get_provider("cmems")
        convert(
            provider=provider,
            input_source=med_file,
            output_dir=out,
            verbose=True,
            yes=True,
            force_reprocess=True,
        )
        parquets = list(out.rglob("*.parquet"))
        assert parquets, "No parquet files produced for CMEMS Mediterranean data"

    # -- Baltic --

    @pytest.mark.integration
    def test_cmems_bal_convert(self, tmp_path: Path) -> None:
        bal_file = _copy_sample(
            RAW_DATA / "cmems" / "cmems_bal_insitu.csv", tmp_path
        )
        out = tmp_path / "out"
        provider = get_provider("cmems")
        convert(
            provider=provider,
            input_source=bal_file,
            output_dir=out,
            verbose=True,
            yes=True,
            force_reprocess=True,
        )
        parquets = list(out.rglob("*.parquet"))
        assert parquets, "No parquet files produced for CMEMS Baltic data"
