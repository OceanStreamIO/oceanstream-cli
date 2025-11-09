import pytest

from pydantic import ValidationError

from oceanstream.providers.models import GeoParquetData, OceanographicMeasurement


def test_oceanographic_measurement_minimal_required_fields():
    m = OceanographicMeasurement(
        platform_id="sd1030",
        latitude=1.25,
        longitude=175.5,
        timestamp="2023-08-01T00:00:00Z",
    )
    assert m.temperature is None
    assert m.salinity is None
    assert m.depth is None
    assert m.other_measurements is None


def test_oceanographic_measurement_type_validation():
    # Valid coercible types should parse (e.g., strings that represent numbers)
    m = OceanographicMeasurement(
        platform_id="1030",
        latitude="2.5",
        longitude="-160.0",
        timestamp="2023-08-01T01:00:00Z",
        temperature="24.1",
        salinity=None,
        depth=5,
        other_measurements={"u": 0.1},
    )
    assert isinstance(m.platform_id, str)
    assert isinstance(m.latitude, float)
    assert isinstance(m.longitude, float)
    assert isinstance(m.temperature, float)

    # Non-coercible should fail
    with pytest.raises(ValidationError):
        OceanographicMeasurement(
            platform_id="sd",
            latitude="not-a-float",
            longitude=0,
            timestamp="2023-08-01T01:00:00Z",
        )


def test_geoparquet_data_model_nesting():
    m1 = OceanographicMeasurement(
        platform_id="sd1030",
        latitude=0.0,
        longitude=0.0,
        timestamp="2023-08-01T00:00:00Z",
        temperature=20.0,
    )
    m2 = OceanographicMeasurement(
        platform_id="sd1079",
        latitude=1.0,
        longitude=1.0,
        timestamp="2023-08-02T00:00:00Z",
        salinity=35.0,
    )

    gpq = GeoParquetData(
        measurements=[m1, m2],
        latitude_bins=[-2.0, 0.0, 2.0],
        longitude_bins=[-2.0, 0.0, 2.0],
    )

    assert len(gpq.measurements) == 2
    assert gpq.latitude_bins == [-2.0, 0.0, 2.0]
    assert gpq.longitude_bins == [-2.0, 0.0, 2.0]

    # Missing required field should raise
    with pytest.raises(ValidationError):
        GeoParquetData(measurements=[], latitude_bins=[0.0, 1.0])  # type: ignore[call-arg]
