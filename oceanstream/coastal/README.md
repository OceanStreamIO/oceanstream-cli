# Coastal optics scientific beta

A standalone Sentinel-2 library and CLI. **The engineering implementation is available; the scientific beta is not released.** Acceptance requires independent optical evidence at Sesimbra and Donegal. Summer Isles remains a stress test. Pléiades Neo is experimental. Spectral classes have no habitat labels.

## Install and run the executable example

From the **repository root**, using Python 3.11–3.13:

```bash
python -m venv .venv
source .venv/bin/activate
python -m pip install '.[coastal]'
python -m oceanstream.coastal.quickstart --output out/coastal-example
```

The example generates synthetic reflectance and bathymetry, executes the processor, and prints its input and run directories. It should finish with `success: true`, `status: rejected`: it deliberately supplies no field validation, tide prediction, or registered reference regions. Synthetic results demonstrate execution, not accuracy. ACOLITE is a separately installed program; the example does not require it. Add `[coastal-acquire]` for the optional acquisition helpers.

## Real input requirements

- One acquisition per ACOLITE directory. Export matching surface reflectance `rhos` and/or remote-sensing reflectance `Rrs` GeoTIFFs, with identical grids and wavelength sets. The loader reads the acquisition time, solar zenith, and ACOLITE version from raster metadata when present. It checks scene identity, duplicate wavelengths, grids, and paired reflectance values. ACOLITE defines `rhos = pi * Rrs`; `Rrs` is above-water reflectance in sr⁻¹, and `rhos` is dimensionless. [ACOLITE documentation](https://github.com/acolite/acolite).
- Sentinel-2 visible bands, NIR, and both SWIR bands required by the sensor profile. Cloud, land, glint, invalid-value, and deep-water screens use surface reflectance.
- An AOI JSON declaring a wide `processing_bbox`, a smaller `habitat_bbox` for reporting, `depth_valid_range_m`, `deepwater_reference_uri`, and `attenuation_regions_uri`. Bounds are WGS84 `(west, south, east, north)`. Calibration polygons require unique string `region_id` values and must not overlap on the scene grid. All polygons need a CRS. Register regions before inspecting retrieval results.
- Fine and coarse depth rasters separately, in a common vertical datum. Fine depths support local calibration; offshore references prefer coarse coverage. Supply `uncertainty_m`, source attribution, survey information, and `provenance.independent_of_scene: true` for genuinely independent references. Unknown independence or uncertainty downgrades products. The bundled AOIs are discovery records: download/subset their archive/WCS references to actual rasters first. Positive-down/up GeoTIFF tags are preferred; inferred sign is recorded.
- A tide JSON with `offset_m` (water height above the bathymetry datum), `derivation: "model"` or `"declared"`, `source`, timezone-aware `when` matching the acquisition, `uncertainty_m`, and `metadata.vertical_datum`. A fitted offset cannot support independent validation. The library accepts an independent prediction or gauge measurement; constituent grids and an operational FES/TPXO adapter are not bundled.

Relative reference paths in AOI JSON resolve beside that JSON. Uncovered or out-of-domain product pixels remain nodata. Reporting coverage is recorded for the requested area intersecting the scene grid; pixels outside the supplied scene cannot be recovered.

Example tide record (replace all values with measured/predicted values for the acquisition):

```json
{
  "offset_m": 1.2,
  "derivation": "model",
  "source": "named model/version and prediction record",
  "when": "2026-06-27T11:30:46.405805Z",
  "uncertainty_m": 0.15,
  "metadata": {"vertical_datum": "LAT"}
}
```

## CLI

```bash
oceanstream process coastal aoi sesimbra --describe aois/sesimbra.json
oceanstream process coastal detect --aoi aois/sesimbra.json \
  --scene data/acolite/2026-06-27 --tide tide.json --config config.json \
  --output-dir out/sesimbra --fail-on-reject

oceanstream process coastal attenuation --acolite-dir data/acolite/2026-06-27 \
  --bathymetry data/fine_depth.tif --aoi aois/sesimbra.json \
  --tide tide.json --config config.json --output-dir out/attenuation

oceanstream process coastal detectability \
  --attenuation out/sesimbra/runs/RUN_ID/attenuation.json --region reef \
  --output-dir out/detectability
oceanstream process coastal qc --run-dir out/sesimbra
```

`detect` and `attenuation` use the same processor. `attenuation` also writes diagnostic products required by that processing path. With an AOI, its reference declarations take precedence over the legacy `--bathymetry` argument. `detectability` preserves serialized calibration and product flags; overriding geometry or noise produces a sensitivity scenario that requires revalidation. The comparison harness shares the input and attenuation stages and summarizes one registered region per call.

Configuration is JSON with any of these sections: `retrieval`, `mask`, `bathymetry`, `attenuation`, `qc`, `detectability`, `uncertainty`. Keys match the dataclasses in [config.py](config.py). Missing sections retain defaults. Model uncertainty is **not** guessed: `uncertainty.empirical_relative_sigma`, `uncertainty.qaa_relative_sigma`, and `uncertainty.source` must describe independently estimated model discrepancy. A convenient percentage with no supporting study is not a validated error model.

Exit codes: `0` execution completed, `1` execution failed, `2` insufficient inputs, `3` QC rejection when requested by `detect --fail-on-reject`, or for a failed `qc` check. A zero exit alone does not mean scientifically accepted.

## Library

```python
from oceanstream.coastal import AOI, Scene, CoastalProcessor
from oceanstream.coastal.commands import read_tide
from pathlib import Path

aoi = AOI.from_json("aois/sesimbra.json")
scene = Scene.from_acolite_dir("data/acolite/2026-06-27")
result = CoastalProcessor().run(
    aoi, scene, "out/sesimbra", tide_correction=read_tide(Path("tide.json"))
)
print(result.success, result.status, result.product_verdicts)
```

## Product definitions and dependencies

| Product | Meaning and scientific dependency |
|---|---|
| `attenuation.json` | Regional, band-specific empirical **two-way** k, in m⁻¹, fitted to independent reference depths. Includes raw fits, quality flags, fit windows, censored residual counts, depth-bin support, and conditional regression errors. This is not the downwelling coefficient Kd. |
| `detectability.json`, `detectability_margin`, `detectable` | Contrast detection limit from empirical k, measured reflectance noise, and declared substrate endmembers. Margin is z_max minus retrieval depth. Endmember/noise perturbations are sensitivity scenarios, not confidence limits. |
| `sdb_depth`, `sdb_depth_sigma` | Raw satellite-only Stumpf depth and conditional uncertainty. Scored against spatially withheld reference blocks **before blending**. `sdb_partition` records calibration/evaluation membership; interpolation boundaries are excluded. |
| `retrieval_depth`, `depth_sigma` | Depth used by inversion: reference/SDB blend where supported, otherwise valid reference. Missing satellite estimates cannot become zero-depth observations. Shared reference error is retained conservatively. |
| `rho_b_WAVELENGTH`, `rho_b_sigma_WAVELENGTH` | Effective bottom reflectance from the QAA/Lee path, for visible bands. It is not laboratory albedo or a direct species observation. |
| `seabed_par`, `seabed_par_sigma` | **Fraction of subsurface PAR reaching the bottom**, using QAA-derived attenuation and the declared spectral weighting. Absolute irradiance requires a separate surface-irradiance input. |
| `valid_water`, `analysis_region`, `qc.json` | Masks, region membership, coverage, reference-contamination diagnostics, terrain substrate controls, empirical/QAA comparison, and uncertainty budgets. |
| `spectral_class` | Exploratory spectral clusters only. |

Uncertainty rasters are conditional one-sigma propagation under the assumptions recorded in `qc.json`/`report.json`. They include depth/reference/tide terms, reflectance noise sensitivity, conditional fit errors, and optional independently estimated model discrepancy. Spatially correlated errors, the suitability of substrate assumptions, and model discrepancy require benchmark evidence. Partial budgets remain labeled, and missing prerequisites prevent product acceptance. Regression standard errors condition on the supplied reference depths; they do not by themselves capture depth calibration bias. Uniform additive subtraction is a diagnostic, not an empirical-k repair: the offset cancels from `L - L_inf` when applied to both.

## Output schema 2.0 and failures

Each processor call writes `OUTPUT/runs/TIME-ID/`. Only a run whose required products, STAC, and report have finished receives `manifest.json`; `OUTPUT/latest.json` then points to it atomically. Failed reruns keep their own `failure.json` and do not change the last complete run. Partial files in a failed run are diagnostics, not a complete dataset. The full processor requires local output; copy a completed run to object storage afterwards.

Each product has its own verdict. JSON and STAC use `oceanstream:status` and QC properties; rasters use `OCEANSTREAM_STATUS`, `OCEANSTREAM_QC_PASSED`, and `OCEANSTREAM_QC_SUMMARY`. `publishable` means the configured product checks passed; it is not a scientific release certification. A completed scene may contain products with different verdicts. Historical raw-k JSON without fit-quality metadata stays diagnostic when reopened.

The report records array/input hashes, masks, scene and time, bathymetry, tide, configuration, code hash, software versions, and ACOLITE settings hashes. STAC assets resolve relative to their item files and are tested against the declared [STAC 1.0](https://github.com/radiantearth/stac-spec/tree/v1.0.0) and extension schemas.

## Benchmark and release gate

See [the benchmark protocol](../../benchmarks/coastal-beta/README.md) and [implementation status](../../docs/coastal/beta-status.md). Preserve the historical Sesimbra golden fixture: it verifies behavior, including known biases. Independent reference cases belong in separate fixtures and reports.

## Supplied-input benchmark and validation scope

The [six-scene registration](../../benchmarks/coastal-beta/README.md#supplied-six-scene-baseline)
connects the existing Sesimbra, Donegal and Summer Isles inputs for a diagnostic
benchmark. See [practical optical validation](../../docs/coastal/field-validation.md)
for research-beta limits and a staged field-campaign design. Successful execution
does not imply accepted physical products.
