# Six-scene supplied-input benchmark

Executed 2026-09-15. **All six scenes completed processing; none of the five
physical product types passed scientific acceptance.** The run is a diagnostic
baseline for a research-software beta. It does not establish validated optical
accuracy or satisfy the original two-site scientific release gate.

## Results

| Site / date | Execution | Deep reference samples | Eligible reporting coverage | Raw SDB MAE / RMSE (m) | SDB span collapsed |
|---|---|---:|---:|---:|---|
| Sesimbra 2026-06-17 | Complete, rejected | 5,000 | 10.1% | 3.85 / 4.78 | No |
| Sesimbra 2026-06-27 | Complete, rejected | 5,000 | 37.5% | 4.11 / 5.02 | No |
| Donegal 2026-07-15 | Complete, rejected | 4,019 | 42.9% | 3.21 / 4.11 | Yes |
| Donegal 2026-08-24 | Complete, rejected | 147 | 41.6% | 3.56 / 4.50 | Yes |
| Summer Isles 2026-05-27 | Complete, rejected | 5,000 | 17.5% | 5.27 / 6.19 | Yes |
| Summer Isles 2026-09-09 | Complete, rejected | 5,000 | 17.4% | 5.45 / 6.32 | Yes |

**Coverage definition:** water pixels with reference depths within 0–40 m, divided
by all requested reporting-rectangle pixels on the scene grid. The denominator
includes land and masked pixels. These are provisional reporting rectangles, not
surveyed habitat areas. This is input eligibility, **not accepted product coverage**;
accepted coverage is zero for all five physical products on every date.

**SDB error definition:** unblended satellite depth compared with withheld native
reference blocks across the scene in the configured 2–25 m reference window.
These scores are conditional agreement with the supplied bathymetry. They are not
independent instantaneous-depth accuracy: acquisition-time tides and measured
reference uncertainty are missing. The many raster pixels are correlated and are
not independent soundings. Published error rows do not make the collapsed northern
SDB maps acceptable; inversion falls back to reference depth for those scenes.

## Measured problems and next priorities

1. **Resolve optical reference quality at Sesimbra.** Both dates flag
   `deep_mask_contaminated`, lack usable visible-band attenuation fits, and have
   implausible QAA particle backscatter. June 17 has no invertible reporting
   pixels. Inspect spectra and spatial variability within each existing offshore
   patch, together with glint/cloud/adjacency diagnostics and the atmospheric
   correction settings. Record why patches fail; do not select replacements solely
   because they produce plausible k. A uniform additive offset cannot repair the
   empirical residual fit.
2. **Investigate the northern depth-correlated signal.** Donegal still fails the
   attenuation gradient and physical-floor checks with local fitting. Both dates
   produce collapsed SDB spans. Summer Isles also has below-floor, unstable or
   weak fits and collapsed SDB; its September date flags the gradient diagnostic.
   Preserve these failures. Check substrate controls, optical signal windows,
   water variability and native depth coverage before proposing another frozen
   diagnostic experiment. Do not lower thresholds to secure acceptance.
3. **Supply defensible corrections, rather than more duplicate input files.**
   The imagery and bathymetry are connected. Acquisition-time LAT water levels,
   justified depth uncertainty and optical model uncertainty remain unresolved.
   Until supplied, depth/PAR/reflectance errors and uncertainty are conditional.
   The configured 3 m fallback reference sigma is an assumption, not a measured
   uncertainty budget. Donegal August has only 147 screened reference pixels;
   audit spatial support and temporal variability as well as the count.
4. **Keep beta claims practical.** Continue engineering against this repeatable
   benchmark with experimental optical outputs. Independent optical accuracy
   claims require additional evidence. The [field-validation plan](field-validation.md)
   describes a small Sesimbra depth/PAR pilot and a larger spectral campaign;
   neither is silently substituted by diver quadrats or these depth comparisons.

The inspected final ACOLITE settings agree across all six dates: dark-spectrum
correction, tiled aerosol estimation, intercept spectrum selection, the same listed
LUTs, 10 m output, and residual glint correction disabled. Those inspected settings
are a starting point for diagnosis, not proof that glint caused the failures or
that the historical code was identical. Each recorded build string and every
settings file remain in the frozen provenance.

## Connected inputs and engineering fixes

- Separate supplied fine and coarse bathymetry at all three sites; no imagery-
  derived depth used as attenuation calibration truth.
- Existing Sesimbra deep-water patches and provisional reporting rectangle.
  Calibration uses each site's existing screening bounds. Northern reference
  polygons use coarse depth >50 m outside reporting, clipped to the supplied
  processing extent. Regions remain explicitly exploratory.
- Reference polygons now apply **before** capped random sampling. Sesimbra has
  26,589 and 24,249 qualifying pixels in the existing patches; the old order kept
  only 18 and 17 by sampling the whole scene first. The corrected draw uses 5,000
  per date. Minimum counts and physical checks were not relaxed.
- Generated northern polygons discard non-area clipping remnants and normalize
  coordinates to sub-millimetre precision before validating in each scene CRS.
- Failed runs retain preparation diagnostics. Completed runs record elapsed time,
  provenance, per-product reasons and correct retrieval-depth source metadata.

## Reproduce and inspect

- [Registration](../../benchmarks/coastal-beta/supplied-v3/benchmark.json) and
  [frozen lock](../../benchmarks/coastal-beta/supplied-v3/benchmark-v3.lock.json).
- [Portable results summary](six-scene-results.json): scores, counts, fit-quality
  flags and per-product reasons. Paths inside this export identify local artifacts.
- [Full local benchmark report](../../out/coastal-beta/supplied-v3/runs/20260915T152610-40b881ba9424/benchmark.json)
  and each scene's isolated run directory contain JSON, rasters, STAC and manifests.
- [Registration and execution instructions](../../benchmarks/coastal-beta/README.md#supplied-six-scene-baseline).
  Use a new registration when code or inputs change. V1/V2 remain archived
  diagnostics of defects discovered during integration; V3 is the current baseline.

```bash
python -m oceanstream.coastal.benchmark run \
  benchmarks/coastal-beta/supplied-v3/benchmark-v3.lock.json \
  out/coastal-beta/supplied-v3-reproduction
python scripts/coastal/summarize_benchmark.py \
  out/coastal-beta/supplied-v3/runs/20260915T152610-40b881ba9424/benchmark.json \
  docs/coastal/six-scene-results.json
```

The benchmark command returns **2** because the scientific evidence gate is
incomplete, while all six scene records have `success: true`, `status: rejected`.
The JSON gate remains `evidence_complete: false`, `release_ready: false`.

## Verification

- Coastal suite: **767 passed, 2 skipped, 1 existing unexpected pass**; the
  reference-sampling regression also verifies failure diagnostics and no completed
  manifest on insufficient data.
- Mypy: **44 coastal files passed**. Required Ruff checks and whitespace checks passed.
- Current wheel built and installed in the isolated Python 3.12 environment;
  dependency checks and all five CLI smoke commands passed outside the checkout.
- All **12 real-run STAC documents** validate against core and declared extension
  schemas; asset links resolve. Status metadata matches verdicts across **130 rasters**.
- All **270 frozen files** were checksum-verified after execution. Previous runs,
  the historical golden fixture and acceptance thresholds remain preserved.

Earlier Python 3.11/3.13 clean-install verification is recorded in
[beta status](beta-status.md); those installation matrices were not repeated here.
