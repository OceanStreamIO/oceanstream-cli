"""Fixtures for e2e echodata tests using real EK80 raw files."""

from pathlib import Path
import pytest

# Project paths - tests/e2e/conftest.py → parents[3] = project root
PROJECT_ROOT = Path(__file__).resolve().parents[3]
RAW_DATA_DIR = PROJECT_ROOT / "raw_data" / "saildrone-ek80-raw"

# Select one raw file for quick tests, multiple for comprehensive tests
SAMPLE_RAW_FILE = RAW_DATA_DIR / "SD_TPOS2023_v03-Phase0-D20230601-T005958-0.raw"


def pytest_configure(config):
    """Register custom markers."""
    config.addinivalue_line("markers", "e2e: end-to-end tests with real data")


@pytest.fixture(scope="module")
def raw_data_dir() -> Path:
    """Return the raw data directory, skip if not available."""
    if not RAW_DATA_DIR.exists():
        pytest.skip(f"Raw data directory not found: {RAW_DATA_DIR}")
    return RAW_DATA_DIR


@pytest.fixture(scope="module")
def sample_raw_file() -> Path:
    """Return a single sample raw file for quick tests."""
    if not SAMPLE_RAW_FILE.exists():
        pytest.skip(f"Sample raw file not found: {SAMPLE_RAW_FILE}")
    return SAMPLE_RAW_FILE


@pytest.fixture(scope="module")
def all_raw_files(raw_data_dir: Path) -> list[Path]:
    """Return all raw files in the test data directory."""
    files = sorted(raw_data_dir.glob("*.raw"))
    if not files:
        pytest.skip("No .raw files found in raw data directory")
    return files


@pytest.fixture(scope="module")
def echopype_available():
    """Check if echopype is available, skip if not."""
    try:
        import echopype
        return True
    except ImportError:
        pytest.skip("echopype not installed - install the fork to run e2e tests")


@pytest.fixture(scope="module")
def converted_zarr(sample_raw_file: Path, echopype_available, tmp_path_factory) -> Path:
    """Convert sample raw file to zarr (cached for module)."""
    import echopype as ep
    
    output_dir = tmp_path_factory.mktemp("echodata")
    output_path = output_dir / f"{sample_raw_file.stem}.zarr"
    
    # Convert raw to EchoData zarr
    echodata = ep.open_raw(sample_raw_file, sonar_model="EK80")
    echodata.to_zarr(output_path, overwrite=True)
    
    return output_path


@pytest.fixture(scope="module")
def echodata_obj(sample_raw_file: Path, echopype_available):
    """Load sample raw file as EchoData object (cached for module)."""
    import echopype as ep
    
    return ep.open_raw(sample_raw_file, sonar_model="EK80")


@pytest.fixture(scope="module")
def sv_dataset(echodata_obj, echopype_available):
    """Compute Sv from sample EchoData (cached for module)."""
    import echopype as ep
    
    # EK80 requires waveform_mode and encode_mode
    ds_Sv = ep.calibrate.compute_Sv(
        echodata_obj,
        waveform_mode="CW",
        encode_mode="complex",
    )
    return ds_Sv
