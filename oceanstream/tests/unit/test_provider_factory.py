from providers import get_provider, ProviderBase
import pytest


def test_get_saildrone_provider():
    provider = get_provider("saildrone")
    assert provider.name == "saildrone"
    assert hasattr(provider, "enrich_dataframe")


def test_unknown_provider_raises():
    with pytest.raises(ValueError):
        get_provider("unknown_provider")
