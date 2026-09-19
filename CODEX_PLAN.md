# Coastal optics: assessment and path to a scientific beta

## 1. Summary

**The implementation is substantial, but it is not ready for release.** Most planned modules exist and the coastal test suite passes. The main blockers are incorrect acceptance decisions, depth-processing defects, incomplete integration of scientific safeguards, and insufficient independent validation.

The agreed release target is:

- A standalone library and CLI, initially supporting Sentinel-2.
- Accepted attenuation, detectability, SDB, effective bottom reflectance, and seabed PAR products at **Sesimbra and one contrasting AOI**.
- Published errors, uncertainty, and operating limits for each product.
- Donegal as the second release candidate; Summer Isles remains a stress-test site.

This requires both engineering fixes and additional validation evidence.

## 2. Implementation against the original plan

I reviewed the current working tree, including uncommitted changes, against the [coastal optics plan](/Users/andrei/oceanstream/sd-data-ingest/.github/prompts/plan-coastalOpticsLibrary.prompt.md).

| Area | Assessment |
|---|---|
| Core physics and generic AOI/sensor abstractions | Largely implemented, with extensive numerical tests. |
| Raster IO, acquisition, ACOLITE, bathymetry adapters | Implemented, but important input assumptions are not enforced throughout the pipeline. |
| Products, STAC, processor, CLI | Present, with several release-blocking integration defects. |
| Multi-AOI validation | Five runs across three AOIs are recorded. The two-site release criterion is not met. |
| Additive-offset investigation | Useful diagnostic work; a uniform offset cancels from the empirical attenuation fit. It should no longer be the main repair strategy. |
| Packaging and documentation | The root wheel builds and contains coastal modules and AOI files. CI and user documentation remain incomplete. |

**Verification performed:**

- **733 tests passed, 2 skipped, 1 unexpected pass**; coastal coverage **90.1%**.
- Mypy passed across **36 coastal source files**.
- Ruff reported **287 violations**, predominantly existing style categories.
- Repository-wide test collection failed on duplicate `test_s3_storage` module names.
- The comparison harness has **0% coverage** in the coastal suite.
- The historical real-scene runs were reviewed from the plan, not rerun during this assessment.

The golden Sesimbra test demonstrates preservation of prototype behavior—including known biases—not independent physical correctness.

## 3. Prioritized implementation work

### P0 — Fix incorrect outputs and acceptance decisions

These should be the first, bounded change set.

| Confirmed defect | Required correction |
|---|---|
| Four fits marked untrustworthy can still produce a passing scene verdict and `detectability.status = "ok"`. | Preserve fit-quality flags through every consumer. Combine fit validity, physical checks, reference quality, and product-specific prerequisites. Unknown validity must never become acceptance. |
| Missing satellite depths become zero before smoothing. A test with a 20 m reference produced a 2 m blended depth. | Use masked, normalized smoothing and blend only available estimates. Where SDB is missing, retain the valid reference and its uncertainty. |
| `coastal attenuation` crashes when a `SensorProfile` is passed to a string lookup. | Use the scene’s resolved profile directly. |
| `coastal detectability` crashes because it iterates dictionary keys as band objects. | Iterate band values and test the complete command. |
| A STAC write failure returns `execution_failed` with `success=True`. | Mark success only after required outputs finish. Ensure execution failures produce a nonzero CLI exit and cannot leave a completed-run manifest. |

The relevant implementation is in the [processor](/Users/andrei/oceanstream/sd-data-ingest/oceanstream/coastal/processor.py:185), [depth blending](/Users/andrei/oceanstream/sd-data-ingest/oceanstream/coastal/bathymetry/stumpf.py:343), and [CLI commands](/Users/andrei/oceanstream/sd-data-ingest/oceanstream/cli.py:2339).

Introduce **per-product verdicts**. A valid depth product should not automatically certify reflectance or PAR. Retain diagnostic outputs, with explicit reasons and consistent status in JSON, raster metadata, and STAC.

### P1 — Correct the spatial and depth inputs

- Separate the **wide deep-water reference area**, **local attenuation calibration regions**, and **product reporting area**. Use explicit, preregistered region masks; retain whole-scene fitting as a diagnostic.
- Load fine and coarse bathymetry separately. The current processor selects one surface, although fine coastal bathymetry and offshore reference sampling have different coverage needs.
- Use **independent reference depths for attenuation calibration**. The processor currently fits attenuation against depths already blended with SDB derived from the same imagery, creating a circular dependency.
- Enforce the existing AOI reference polygons, reporting bounds, and depth limits. Report coverage within each requested domain; uncovered pixels remain nodata.
- Apply tide/datum correction using acquisition time and record its source and uncertainty. Missing correction must downgrade affected products.
- Validate wavelength correspondence, scene identity, and grids across both `rhos` and `Rrs` stacks. Follow the documented reflectance convention for [ACOLITE outputs](https://github.com/acolite/acolite).
- Wire cloud-contamination and offset diagnostics into reference assessment. Keep uniform-offset subtraction out of the empirical-k repair strategy.

### P2 — Establish scientifically defensible retrievals

- Fit attenuation within the preregistered local regions. Port the missing substrate-control diagnostic and empirical-versus-QAA comparison.
- Use band-specific fitting windows with demonstrable signal above noise. Record residual censoring and depth-bin support; do not interpret an opaque red band’s regression as measured attenuation.
- Treat near-unity ratios and opaque-band correlations as diagnostic evidence, with applicability limits. Passing physical bounds is necessary but does not establish accuracy.
- Keep the QAA/Lee pathway for effective bottom reflectance and QAA-derived attenuation for PAR explicit. The current pipeline uses empirical k for detectability but QAA for these other products; their validation must follow their actual dependencies.
- Propagate bathymetry, tide, reflectance, attenuation-fit, and model uncertainties. Publish uncertainty products and distinguish sensitivity scenarios from confidence intervals.
- Define seabed PAR as the **fraction of subsurface PAR reaching the bottom**. Absolute irradiance requires an additional surface-irradiance input.
- Preserve the historical golden fixture and add separately validated reference cases rather than replacing the old expectations merely to obtain passing tests.

### P3 — Make the beta reproducible and installable

- Consolidate the processor, CLI, and comparison harness onto shared processing stages. Their current differences in depth preparation and deep-water screening undermine comparisons.
- Version the output schema; preserve calibration flags and provenance when reopening products.
- Record input checksums, scene/time, bathymetry source, configurations, region masks, software versions, and ACOLITE settings.
- Write runs into isolated directories and finalize a manifest only after successful completion. Prevent stale files from earlier runs appearing in new results.
- Correct [CI](/Users/andrei/oceanstream/sd-data-ingest/.github/workflows/ci.yml) to install from the root project with coastal extras; it currently targets the older nested project.
- Add clean wheel-install tests, all five CLI commands, dependency-resolution checks for supported Python versions, and validation against the declared [STAC schemas](https://github.com/radiantearth/stac-spec).
- Add an executable quickstart, input requirements, product definitions, failure interpretation, supported conditions, and release notes. Reconcile stale module and architecture documentation.

## 4. Validation and release gates

**Engineering gate**

- Regression tests reproduce and then prevent every P0 defect.
- Cover missing depth, invalid references, mismatched bands/grids, all-invalid fits, opaque bands, failed writes, reruns, and disagreement between product verdicts.
- CLI and comparison results agree with the processor for identical inputs.
- Clean installation and required CI checks pass.

**Scientific gate**

- Freeze benchmark regions, configurations, and evaluation splits before comparing results.
- Run at least two dates at Sesimbra and two at Donegal; retain Summer Isles failures in the benchmark.
- Validate SDB on spatially withheld reference observations, keeping shared source bathymetry cells out of opposing splits. Score the SDB estimate before blending it with its validation reference.
- Obtain independent optical evidence for attenuation, effective bottom reflectance, and PAR at both release sites. Bathymetry and diver quadrats alone do not validate all these quantities.
- Validate detectability against held-out contrast-versus-depth observations; retain the substrate-contrast assumptions in every result.
- Publish errors and uncertainty by product, depth, region, and date, alongside accepted coverage and rejection reasons.

**Release remains blocked until the two-site evidence exists.** If local fitting does not resolve Donegal’s failures, retain the rejection and investigate; do not loosen checks to satisfy the release criterion.

## 5. Defaults and boundaries

- Sentinel-2 is the beta support target; Pléiades Neo remains experimental.
- Spectral clusters remain exploratory classes, without habitat labels.
- Processing remains label-free; independent observations are required for validation, not routine operation.
- ACOLITE remains separately installed, with the benchmark version pinned.
- Hosted deployment, EarthStudio/EDITO integration, and large-scale AOI rollout follow the beta.
- Accuracy claims are limited to the measured benchmark results and stated operating conditions.

**Recommended next step:** complete P0 first, then P1 before generating further scientific comparison runs. Arrange the missing two-site optical reference evidence alongside that engineering work—it is a separate dependency for releasing all physical products.
