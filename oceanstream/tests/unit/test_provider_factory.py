from oceanstream.providers import get_provider, ProviderBase
import pytest


def test_get_saildrone_provider():
    """Backward-compatible: get_provider('saildrone') returns NoaaPmelProvider."""
    provider = get_provider("saildrone")
    assert provider.name == "noaa_pmel"
    assert hasattr(provider, "enrich_dataframe")


def test_get_noaa_pmel_provider():
    provider = get_provider("noaa_pmel")
    assert provider.name == "noaa_pmel"
    assert hasattr(provider, "enrich_dataframe")


def test_unknown_provider_raises():
    with pytest.raises(ValueError):
        get_provider("unknown_provider")
