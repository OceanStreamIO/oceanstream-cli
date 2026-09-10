"""Tide — datum correction, and the independence rule that guards validation."""

from __future__ import annotations

import datetime as dt

import numpy as np
import pytest

from oceanstream.coastal.bathymetry.tide import (
    ConstantTide,
    NullTide,
    PyTMDTide,
    TideCorrection,
    correct_depth,
    fit_offset_from_reference,
    get_provider,
)

# Sesimbra per-campaign offsets: LAT-referenced LiDAR minus instantaneous
# water level on each acquisition date.
SESIMBRA_OFFSETS_M = (-1.92, -1.09, -0.58)


class TestTideCorrection:
    def test_valid_correction(self) -> None:
        c = TideCorrection(offset_m=-1.92, derivation="model", source="FES2022")
        assert c.offset_m == pytest.approx(-1.92)
        assert c.is_independent

    @pytest.mark.parametrize("bad", [float("nan"), float("inf"), -float("inf")])
    def test_non_finite_offset_rejected(self, bad: float) -> None:
        with pytest.raises(ValueError, match="finite"):
            TideCorrection(offset_m=bad, derivation="model", source="x")

    def test_implausible_offset_rejected(self) -> None:
        # No coastal tidal range reaches 15 m; a value that large is a units or
        # sign error that would otherwise propagate into every depth.
        with pytest.raises(ValueError, match="exceeds the plausible range"):
            TideCorrection(offset_m=40.0, derivation="model", source="x")

    def test_offset_at_the_limit_is_allowed(self) -> None:
        TideCorrection(offset_m=15.0, derivation="model", source="x")

    def test_unknown_derivation_rejected(self) -> None:
        with pytest.raises(ValueError, match="Unknown derivation"):
            TideCorrection(offset_m=0.0, derivation="guessed", source="x")  # type: ignore[arg-type]

    def test_declared_offsets_are_independent(self) -> None:
        c = TideCorrection(offset_m=-1.0, derivation="declared", source="operator")
        assert c.is_independent

    def test_fitted_offsets_are_not_independent(self) -> None:
        # A fit against the same reference the depths are validated against
        # cannot then be used to validate them.
        c = TideCorrection(offset_m=-1.48, derivation="fitted", source="diver quadrats")
        assert not c.is_independent

    def test_to_dict_is_json_safe(self) -> None:
        import json

        c = TideCorrection(
            offset_m=-1.92,
            derivation="model",
            source="FES2022",
            when=dt.date(2026, 6, 27),
            uncertainty_m=0.1,
        )
        assert json.loads(json.dumps(c.to_dict()))["offset_m"] == pytest.approx(-1.92)


class TestApply:
    def test_shifts_a_lat_depth_to_the_instantaneous_datum(self) -> None:
        c = TideCorrection(offset_m=-1.92, derivation="model", source="FES2022")
        np.testing.assert_allclose(c.apply(np.array([10.0, 20.0])), [8.08, 18.08])

    def test_scalar_input(self) -> None:
        c = TideCorrection(offset_m=-1.0, derivation="model", source="x")
        np.testing.assert_allclose(c.apply(np.float32(5.0)), 4.0)

    def test_nans_out_depths_driven_to_or_below_the_surface(self) -> None:
        # A negative "depth" is above water; leaving it as a number would let a
        # land pixel enter the retrieval as very shallow water.
        c = TideCorrection(offset_m=-2.0, derivation="model", source="x")
        out = c.apply(np.array([1.0, 2.0, 3.0]))
        assert np.isnan(out[0])
        assert np.isnan(out[1])
        assert out[2] == pytest.approx(1.0)

    def test_preserves_existing_nan(self) -> None:
        c = TideCorrection(offset_m=-1.0, derivation="model", source="x")
        assert np.isnan(c.apply(np.array([np.nan, 10.0]))[0])

    def test_a_zero_offset_is_a_no_op_on_valid_depths(self) -> None:
        out = NullTide().water_level(-9.2, 38.41, dt.date(2026, 6, 27)).apply(np.array([5.0, 12.0]))
        np.testing.assert_allclose(out, [5.0, 12.0])


class TestCorrectDepth:
    def test_applies_the_offset(self) -> None:
        c = TideCorrection(offset_m=-1.09, derivation="model", source="FES2022")
        np.testing.assert_allclose(correct_depth(np.array([10.0]), c), [8.91])

    def test_refuses_a_fitted_offset_on_a_validation_path(self) -> None:
        # This is the circularity guard: fitting the offset to the reference and
        # then scoring against that reference measures nothing.
        c = TideCorrection(offset_m=-1.48, derivation="fitted", source="diver quadrats")
        with pytest.raises(ValueError, match="requires an independent one"):
            correct_depth(np.array([10.0]), c, require_independent=True)

    def test_allows_a_model_offset_on_a_validation_path(self) -> None:
        c = TideCorrection(offset_m=-1.48, derivation="model", source="FES2022")
        np.testing.assert_allclose(
            correct_depth(np.array([10.0]), c, require_independent=True), [8.52]
        )

    def test_allows_a_fitted_offset_when_not_validating(self) -> None:
        c = TideCorrection(offset_m=-1.48, derivation="fitted", source="diver quadrats")
        np.testing.assert_allclose(correct_depth(np.array([10.0]), c), [8.52])


class TestProviders:
    def test_constant_tide_returns_what_it_was_given(self) -> None:
        c = ConstantTide(offset_m=-1.92).water_level(-9.2, 38.41, dt.date(2026, 6, 27))
        assert c.offset_m == pytest.approx(-1.92)
        assert c.derivation == "declared"

    def test_constant_tide_records_the_date_it_was_asked_about(self) -> None:
        when = dt.date(2026, 6, 27)
        assert ConstantTide(offset_m=-1.0).water_level(-9.2, 38.41, when).when == when

    def test_null_tide_is_zero_and_says_why(self) -> None:
        # Zero is a legitimate choice for depth-invariance tests, which care
        # about the gradient rather than the datum — but it has to be recorded
        # as a decision, not left as an unexamined default.
        c = NullTide().water_level(-9.2, 38.41, dt.date(2026, 6, 27))
        assert c.offset_m == 0.0
        assert "no tide" in c.source.lower() or "uncorrected" in c.source.lower()

    def test_pytmd_is_declared_but_not_implemented(self) -> None:
        with pytest.raises(NotImplementedError):
            PyTMDTide().water_level(-9.2, 38.41, dt.date(2026, 6, 27))

    @pytest.mark.parametrize("name", ["constant", "none", "null"])
    def test_get_provider_dispatch(self, name: str) -> None:
        kwargs = {"offset_m": -1.0} if name == "constant" else {}
        assert get_provider(name, **kwargs) is not None

    def test_unknown_provider_names_the_options(self) -> None:
        with pytest.raises(KeyError, match="Known"):
            get_provider("fes2014")


class TestFitOffset:
    def test_recovers_a_known_offset(self) -> None:
        reference = np.array([4.0, 6.0, 8.0, 10.0, 12.0])
        modelled = reference + 1.48
        c = fit_offset_from_reference(modelled, reference)
        assert c.offset_m == pytest.approx(-1.48, abs=1e-6)

    def test_reproduces_the_sesimbra_regression(self) -> None:
        # hr = 1.013*diver - 1.48, from the prototype's quadrat comparison.
        diver = np.array([3.0, 5.0, 8.0, 11.0, 14.0])
        hr = 1.013 * diver - 1.48
        c = fit_offset_from_reference(hr, diver)
        assert c.metadata["slope"] == pytest.approx(1.013, abs=1e-3)

    def test_is_always_marked_fitted(self) -> None:
        c = fit_offset_from_reference(np.array([1.0, 2.0, 3.0]), np.array([2.0, 3.0, 4.0]))
        assert c.derivation == "fitted"
        assert not c.is_independent

    def test_carries_the_circularity_warning(self) -> None:
        c = fit_offset_from_reference(np.array([1.0, 2.0, 3.0]), np.array([2.0, 3.0, 4.0]))
        assert "circularity_warning" in c.metadata

    def test_records_the_sample_size(self) -> None:
        c = fit_offset_from_reference(np.arange(5.0) + 1.0, np.arange(5.0) + 2.0)
        assert c.metadata["n_points"] == 5

    def test_ignores_non_finite_pairs(self) -> None:
        modelled = np.array([2.0, 3.0, 4.0, np.nan, 6.0])
        reference = np.array([1.0, 2.0, 3.0, 4.0, np.nan])
        assert fit_offset_from_reference(modelled, reference).metadata["n_points"] == 3

    def test_mismatched_lengths_rejected(self) -> None:
        with pytest.raises(ValueError, match="same number of points"):
            fit_offset_from_reference(np.array([1.0, 2.0]), np.array([1.0]))

    def test_too_few_pairs_rejected(self) -> None:
        with pytest.raises(ValueError, match="at least"):
            fit_offset_from_reference(np.array([1.0, 2.0]), np.array([2.0, 3.0]))
