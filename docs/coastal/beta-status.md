# Coastal scientific beta: implementation status

Updated 2026-09-16. **The supplied inputs are connected and the six-scene diagnostic
benchmark is executable. The full scientific beta remains blocked.** See
[the benchmark report](six-scene-benchmark.md) and
[practical field-validation options](field-validation.md). No publication,
deployment, or independent real-scene accuracy claim is made by this change.

The [Key West published-reference experiment](key-west-reference.md) now provides
a successful SDB component benchmark: spatially calibrated blue/red retrievals
achieve about 0.35 m MAE on withheld blocks, while the current blue/green setup is
near a constant-depth baseline. These conditional single-scene results identify
a concrete configuration improvement; they do not validate the other optical products.

The [Key West fixed-depth optical experiment](key-west-optics.md) also completed
under both corrections. Six of twelve local visible-band attenuation fits pass
individual checks per correction, but regional multi-band verdicts fail and both
QAA estimates trigger the backscatter plausibility warning. Green/red bottom
reflectance has no retained coverage at 5–7.5 m. All optical outputs remain
diagnostic; the supplied lidar and an independent gauge enabled these tests
without a new field campaign.

The [Key West bathymetry replication/improvement benchmark](key-west-replication.md)
now evaluates 24 frozen models on fresh adjacent strips. Nine-point robust fitting
reaches 0.32–0.33 m median error. With a separate 10,000-point calibration budget,
combined ratios reach 0.22–0.25 m, with deeper-water and regional tradeoffs. These
are lidar-calibrated spatial-transfer results; original chart calibration and a
second-date confirmation remain missing. Production defaults are unchanged.

## Delivered

| Priority | Implementation |
|---|---|
| P0 | Fit-quality flags survive every high-level consumer and serialization. Unknown validity stays rejected. Masked normalized SDB smoothing preserves missingness and the reference's uncertainty. Both CLI crashes are fixed. Required-write failures return execution failure; only completed runs receive manifests. Product verdicts remain separate. |
| P1 | Processor and comparison share independent fine/coarse depth preparation, reference screening, local calibration polygons, reporting masks and depth limits. Tide corrections require matching acquisition time and datum. ACOLITE stacks are checked for identity, wavelengths, grids and reflectance convention. Reference contamination and offset diagnostics are included. |
| P2 foundations | Local band-specific signal windows, residual censoring/bin support, conditional fit errors, terrain substrate controls, empirical/QAA comparisons, uncertainty rasters, raw SDB scoring and native-reference-block splits are implemented. QAA/Lee reflectance and QAA PAR remain explicit dependencies. The original golden fixture is preserved. |
| P3 | Shared CLI stages, output schema 2.0, input/code/version provenance, isolated runs, atomic completion pointers, root-wheel CI, all five CLI smoke checks, offline STAC schema validation, an executable example, and a frozen benchmark/evidence runner are implemented. |

Additional install/schema defects found during verification were corrected: eager unrelated geotrack/NMEA imports, undeclared core Pydantic dependency, missing NumPy support for Python 3.13, an invalid STAC Processing field, and asset paths relative to the wrong directory. SDB maps that collapse or lack held-out validation no longer contaminate the reference depth used by other products.

## Verification

- Latest coastal suite: **767 passed, 2 skipped, 1 existing unexpected pass**.
  The previous implementation's coverage run measured **89.9%** overall and
  **79.8%** for the comparison harness; coverage was not remeasured in this run.
- Mypy: **44 coastal source files passed**.
- Required coastal Ruff rules (`E,F,I,UP`): passed. Existing scientific-style `PL` categories are not part of this CI gate; this is not a claim that repository-wide Ruff is clean.
- Adjacent CLI/provider/R2R regression tests: **68 passed**.
- Repository-wide collection: **2,161 tests collected**, using the configured importlib mode. The full repository suite was not executed.
- Clean wheel installation, dependency checks, executable example, and all five CLI commands passed on **Python 3.11, 3.12 and 3.13**. The coastal suite also passed from installed wheels outside the checkout on Python 3.12 and 3.13.
- Emitted Item and Collection documents validate offline against STAC 1.0.0 and every declared extension schema. Local asset links resolve from the item directory.

Tests include rejected and unknown fits, missing SDB, spatially varying reference uncertainty, mixed acquisition/grid/band stacks, fine/coarse coverage, tide time/datum checks, explicit regions/depth limits, failed STAC writes, reruns, differing product verdicts, CLI/processor/comparison agreement, source-cell split leakage, evidence units and missing coverage, frozen-input changes, and incomplete scientific evidence.

## What still prevents release

1. **Corrections and evaluation registration.** Local benchmark AOIs now connect
   the supplied imagery, separate fine/coarse bathymetry and Sesimbra polygons.
   Northern reference masks use bathymetry; calibration/reporting use existing
   screening rectangles. These regions are explicitly exploratory/provisional.
   Acquisition-time tide corrections and measured depth uncertainty remain
   missing and flagged. A new held-out evaluation is needed for accuracy claims.
2. **Independent optical evidence for the full scientific release.** The supplied
   Sesimbra campaign inputs are available. Qualifying two-site matchups for
   attenuation, bottom reflectance, PAR and contrast versus depth have not yet
   been established. A research-software beta can retain experimental
   optical products and explicit limitations. A staged Sesimbra depth/PAR pilot
   is a practical first field option; a full optical campaign is a separate effort.
3. **Calibrated uncertainty and operating limits.** Current uncertainty propagation is conditional, including declared model-discrepancy terms where available. Conditional regression error does not itself measure depth calibration bias, spatial covariance, endmember uncertainty, or QAA model error. Estimate these terms independently, assess interval calibration, and publish measured errors and coverage by product/depth/region/date. Do not substitute arbitrary percentages for that evidence.
4. **Historical atmospheric-correction provenance.** The diagnostic benchmark
   preserves and verifies each scene's recorded ACOLITE build string and hashes
   its supplied rasters/settings. The different strings do not establish either
   different code or identical code. Reconcile actual historical commits/settings,
   or regenerate with one pinned version, for a controlled scientific comparison.
5. **Scientific review of the completed benchmark.** Require at least two dates at each release site and retain Summer Isles failures. Score raw SDB before blending, with native reference cells excluded across opposing splits. Review sample independence, uncertainty, accepted coverage and operating limits for every product. Retain a Donegal rejection if the evidence warrants it.

The benchmark tool records evidence readiness and keeps `release_ready` false pending that scientific review. It does not manufacture measurements or approve a release solely because processing completed.

## Next executable work

1. Audit the Key West offshore spectra, atmospheric/glint correction and QAA
   applicability using the [completed optical experiment](key-west-optics.md).
   Retain its failures and preregister controlled comparisons; surveyed depth
   already rules out SDB blending as the explanation for those failures.
2. Use the [fresh-region SDB comparison](key-west-replication.md) to register a
   second-date or shallow Sesimbra confirmation of the robust linear and
   combined-ratio candidates, keeping calibration budgets and depth/coverage
   tradeoffs explicit. Integrate the selected configuration into shared stages
   after confirmation.
   Review [the measured six-scene failures and coverage](six-scene-benchmark.md).
   Fix processing defects without loosening acceptance checks; retain failed runs.
3. Resolve acquisition-time LAT water levels and reference uncertainty at the
   original release sites, then
   register another diagnostic comparison. Missing optical field data need not
   delay this engineering work.
4. Use [the field-validation options](field-validation.md) to decide which accuracy
   claims are practical to support. Freeze fresh evaluation dates/regions before
   using improvements to claim scientific accuracy.

## Compatibility / release notes

- Full processor output is now under `runs/TIME-ID/`; resolve the most recent completed run through `latest.json` or `resolve_run`.
- Output schema is **2.0**. Raw SDB and retrieval/blended depth are separate products, with explicit uncertainties and evaluation partitions.
- Visible bottom-reflectance products stop at 700 nm. Opaque longer wavelengths remain reference/artefact diagnostics.
- Legacy numeric-only attenuation JSON remains readable but cannot establish acceptance without saved fit/product validity.
- Reruns preserve earlier outputs and need no overwrite confirmation. The full processor writes locally for atomic completion; sync finished runs afterwards.
- Defaults are intentionally diagnostic when corrections, regions, independence or uncertainty are unverified.
- Sentinel-2 is the beta target. Pléiades Neo, habitat labels, hosted deployment and broad AOI rollout remain outside this release gate.

Start with [the executable quickstart](../../oceanstream/coastal/README.md).
