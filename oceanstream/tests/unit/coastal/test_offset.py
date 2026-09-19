"""Deep-water additive offset (Phase 5)."""

from __future__ import annotations

import numpy as np
import pytest

from oceanstream.coastal.config import QCConfig
from oceanstream.coastal.qc.offset import AdditiveOffset, deepwater_additive_offset

SHAPE = (20, 20)


def _scene(offset: float, *, nir: tuple[float, ...] = (833.0, 865.0)) -> dict:
    """Visible bands carrying signal, NIR bands carrying only the offset."""
    bands = {444.0: np.full(SHAPE, 0.03), 561.0: np.full(SHAPE, 0.02)}
    for wl in nir:
        bands[wl] = np.full(SHAPE, offset)
    return bands


def _deep() -> np.ndarray:
    mask = np.zeros(SHAPE, dtype=bool)
    mask[:10, :] = True
    return mask


def test_recovers_a_uniform_offset() -> None:
    result = deepwater_additive_offset(_scene(0.0195), _deep())
    assert result.value == pytest.approx(0.0195)
    assert result.trustworthy
    assert result.n_pixels == 200


def test_ignores_visible_bands() -> None:
    """The visible bands are 1.5x brighter; reading them would inflate it."""
    result = deepwater_additive_offset(_scene(0.005), _deep())
    assert result.value == pytest.approx(0.005)
    assert set(result.by_band) == {833.0, 865.0}


def test_samples_only_the_deep_mask() -> None:
    bands = _scene(0.004)
    bands[833.0] = bands[833.0].copy()
    bands[833.0][10:, :] = 0.5  # shallow half, excluded by the mask
    bands[865.0] = bands[865.0].copy()
    bands[865.0][10:, :] = 0.5
    assert deepwater_additive_offset(bands, _deep()).value == pytest.approx(0.004)


def test_no_nir_band_yields_zero_not_a_guess() -> None:
    result = deepwater_additive_offset({444.0: np.full(SHAPE, 0.03)}, _deep())
    assert result.value == 0.0
    assert result.qa_flags == ("no_nir_band",)
    assert not result.trustworthy


def test_all_nan_over_the_mask_is_reported() -> None:
    bands = _scene(0.004)
    for wl in (833.0, 865.0):
        bands[wl] = np.full(SHAPE, np.nan)
    result = deepwater_additive_offset(bands, _deep())
    assert result.qa_flags == ("no_finite_pixels",)


def test_nan_pixels_do_not_poison_the_median() -> None:
    bands = _scene(0.004)
    bands[833.0] = bands[833.0].copy()
    bands[833.0][0, :5] = np.nan
    assert deepwater_additive_offset(bands, _deep()).value == pytest.approx(0.004)


def test_negative_offset_is_clamped_and_flagged() -> None:
    result = deepwater_additive_offset(_scene(-0.002), _deep())
    assert result.value == 0.0
    assert "clamped_negative" in result.qa_flags


def test_small_sample_is_flagged_but_still_returned() -> None:
    mask = np.zeros(SHAPE, dtype=bool)
    mask[0, :10] = True
    result = deepwater_additive_offset(_scene(0.006), mask)
    assert result.value == pytest.approx(0.006)
    assert "insufficient_pixels" in result.qa_flags


def test_spectrally_varying_residual_is_flagged() -> None:
    """A scalar cannot describe a residual that differs between NIR bands."""
    bands = _scene(0.004)
    bands[865.0] = np.full(SHAPE, 0.012)
    result = deepwater_additive_offset(bands, _deep())
    assert "spectrally_varying" in result.qa_flags


def test_flat_nir_is_not_flagged_as_varying() -> None:
    bands = _scene(0.004)
    bands[865.0] = np.full(SHAPE, 0.0045)
    assert "spectrally_varying" not in deepwater_additive_offset(bands, _deep()).qa_flags


def test_swir_signal_flags_a_contaminated_sample() -> None:
    """Water is opaque at 1600 nm, so signal there means cloud or glint."""
    bands = _scene(0.004)
    bands[1612.0] = np.full(SHAPE, 0.03)
    result = deepwater_additive_offset(bands, _deep())
    assert "deep_mask_contaminated" in result.qa_flags
    assert result.swir_median == pytest.approx(0.03)
    assert result.value == pytest.approx(0.004), "SWIR must not enter the estimate"


def test_clean_swir_confirms_the_sample() -> None:
    bands = _scene(0.004)
    bands[1612.0] = np.full(SHAPE, 0.001)
    result = deepwater_additive_offset(bands, _deep())
    assert result.trustworthy
    assert result.swir_median == pytest.approx(0.001)


def test_thresholds_are_configurable() -> None:
    bands = _scene(0.004)
    bands[1612.0] = np.full(SHAPE, 0.02)
    cfg = QCConfig(offset_swir_max=0.05)
    assert deepwater_additive_offset(bands, _deep(), config=cfg).trustworthy


def test_nir_cut_excludes_red_edge() -> None:
    """707 and 740 nm still carry water-leaving signal and must not be used."""
    bands = {707.0: np.full(SHAPE, 0.05), 833.0: np.full(SHAPE, 0.004)}
    result = deepwater_additive_offset(bands, _deep())
    assert set(result.by_band) == {833.0}


def test_to_dict_is_serialisable() -> None:
    payload = deepwater_additive_offset(_scene(0.0195), _deep()).to_dict()
    assert payload["additive_offset_rhos"] == 0.0195
    assert payload["by_band_rhos"] == {"833": 0.0195, "865": 0.0195}
    assert payload["trustworthy"] is True


def test_empty_offset_defaults() -> None:
    assert AdditiveOffset(0.0).trustworthy
