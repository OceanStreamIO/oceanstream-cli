"""Markdown assembly for the denoise sensitivity report.

Structure (fixed by the plan):

    Abstract → Background → Prior art → Methods (incl. deviations from
    Ryan 2015) → Results (native support) → Common-support comparison →
    Interpretation (DRAFT) → Conclusions → References → Reproducibility appendix

Every parameter table is generated from the **resolved consumed parameters**
recorded at run time, never transcribed from the TOML, and every TOML field the
wired filters ignore is flagged.

The Interpretation section emits *templated observations only* and is marked
DRAFT until a human signs off — see ``INTERPRETATION_SIGNOFF_MARKER``.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from experiment_contract import BASELINE_PRESET, PRESETS, PRESETS_BY_KEY, TOLERANCES

#: Removing this marker is the manual sign-off gesture for the Interpretation
#: section. Nothing automated may remove it.
INTERPRETATION_SIGNOFF_MARKER = "**[DRAFT — awaiting manual sign-off]**"

PRESET_ORDER = tuple(p.key for p in PRESETS)


# ── Small helpers ──────────────────────────────────────────────────────────


def _fmt(value: Any, spec: str = ".3f", none: str = "—") -> str:
    if value is None:
        return none
    if isinstance(value, bool):
        return "yes" if value else "no"
    if isinstance(value, (int,)) and spec.endswith("d"):
        return format(value, spec)
    try:
        return format(float(value), spec)
    except (TypeError, ValueError):
        return str(value)


def _pct(value: float | None, spec: str = ".3f") -> str:
    return "—" if value is None else f"{float(value) * 100:{spec}}%"


def _table(headers: list[str], rows: list[list[str]]) -> str:
    if not rows:
        return "_No data._\n"
    out = ["| " + " | ".join(headers) + " |",
           "|" + "|".join(["---"] * len(headers)) + "|"]
    out += ["| " + " | ".join(r) + " |" for r in rows]
    return "\n".join(out) + "\n"


def _figure(path: str, caption: str) -> str:
    return f"\n![{caption}]({path})\n\n*{caption}*\n"


def _figures_of(produced: dict, kind: str, contains: str = "") -> list[str]:
    return [p for p in produced.get(kind, []) if contains in p]


def _short(label: str) -> str:
    return label if len(label) <= 34 else label[:31] + "…"


# ── Sections ───────────────────────────────────────────────────────────────


def _abstract(metrics: dict, experiment: dict, day: str) -> str:
    sources = experiment["sources"]
    parts = []
    for cat, entry in sources.items():
        d = entry["description"]
        freqs = ", ".join(
            f"{f / 1000:.0f} kHz" for f in (d.get("frequencies_hz") or [])
        )
        parts.append(
            f"{cat.replace('_', ' ')} ({d.get('n_pings'):,} pings, "
            f"{d.get('acquisition_start_utc')}–{d.get('acquisition_end_utc')} UTC, {freqs})"
        )
    return f"""# Denoise preset sensitivity on a single operational day

**Saildrone TPOS 2023 · {day} · EK80 · tropical central Pacific**

## Abstract

This is a **single-operational-day sensitivity case study**. It quantifies how
sensitive the derived acoustic products — denoised Sv, MVBS and NASC — are to
the choice of denoising preset, faceted by pulse category and physical
frequency. Three presets are applied to one immutable stage-4 Sv source:
the repository's **Ryan-inspired** preset, **tropical_pacific v1**, and
**tropical_pacific v3**. Each arm runs the identical downstream pipeline from
stage 5 onwards under the same code revision.

The study covers {"; ".join(parts)}.

The design deliberately supports **no ranking claim**. One day cannot establish
that any preset is "better"; it can establish how much the products move when
the preset changes, and which filters drive that movement. Where the three arms
agree within the tolerances declared before any results were seen
({TOLERANCES.sv_atol_db} dB for Sv/MVBS, relative {TOLERANCES.nasc_rtol:g} for
NASC), the products are reported as indistinguishable at that resolution.
"""


def _background(experiment: dict, day: str) -> str:
    lines = []
    for cat, entry in experiment["sources"].items():
        d = entry["description"]
        freqs = ", ".join(
            f"{f / 1000:.0f} kHz" for f in (d.get("frequencies_hz") or [])
        )
        gps = d.get("gps", {})
        lat = gps.get("latitude", {})
        lon = gps.get("longitude", {})
        lines.append([
            cat.replace("_", " "),
            f"{d.get('n_pings'):,}",
            f"{d.get('acquisition_start_utc')} → {d.get('acquisition_end_utc')}",
            freqs or "—",
            f"{_fmt(lat.get('min'), '.4f')} … {_fmt(lat.get('max'), '.4f')}",
            f"{_fmt(lon.get('min'), '.4f')} … {_fmt(lon.get('max'), '.4f')}",
        ])

    return f"""
## Background

The TPOS 2023 campaign deployed Saildrone uncrewed surface vehicles across the
tropical Pacific carrying Simrad EK80 wide-band transceivers with hull-mounted
ES38-18|200-18C transducers at a 1.9 m draft. The acoustic environment is
oligotrophic open ocean over roughly 4,500 m of water: a weak deep scattering
layer (DSL) at 300–600 m by day, migrating shallower at night, and a
mesopelagic below the DSL that at 38 kHz sits at the receiver's electronic
noise floor rather than at any acoustic signal.

The day under study, **{day}**, contains two distinct acquisition regimes:

{_table(
    ["Category", "Pings", "Acquisition window (UTC)", "Frequencies", "Latitude", "Longitude"],
    lines,
)}
The two categories are analysed separately throughout. They differ in pulse
length, in channel complement, and in duration by more than an order of
magnitude, so pooling them would blur exactly the sensitivity this study is
trying to measure.
"""


def _prior_art() -> str:
    return """
## Prior art

The filter family used here originates with **Ryan et al. (2015)**
[@ryan2015], which specified four corrections for open-ocean echo integration:
impulse-noise rejection, attenuated-signal (AS) rejection, transient-noise
rejection, and background-noise subtraction after De Robertis & Higginbottom
(2007) [@derobertis2007].

Two independent implementations descend from that paper:

* **echopy** [@echopy] — the original British Antarctic Survey implementation
  of the Ryan filters, and the closest thing to a reference translation of the
  paper into code.
* **echopype** [@echopype] — the maintained migration of that lineage. Its
  `clean` module carries the current implementations, including
  `remove_background_noise` (De Robertis & Higginbottom) and, from PR #1544, a
  dedicated `detect_transient`.

`oceanstream` builds on echopype and reimplements the mask-based filters so
they can be dispatched per channel with frequency-specific parameters, which
echopype's API does not do natively.
"""


def _methods(metrics: dict, experiment: dict, day: str) -> str:
    preset_rows = []
    for p in PRESETS:
        run = metrics["runs"].get(p.key, {})
        preset_rows.append([
            f"`{p.key}`",
            p.label,
            f"`{p.toml_name}`",
            (run.get("preset_toml_sha256") or "—")[:12],
            f"`{run.get('output_container') or '—'}`",
        ])

    param_tables = _parameter_tables(metrics)
    unused = _unused_fields_table(metrics)

    return f"""
## Methods

### Experiment design

All three arms read the **same** stage-4 Sv zarrs — one per pulse category — and
write to their own output container. The source container is opened read-only
and its recursive content hash is re-verified after every arm; a change would
invalidate the comparison and abort the run.

{_table(["Key", "Label", "Preset TOML", "TOML sha256", "Output container"], preset_rows)}
Each arm resumes at **stage 5 (denoise)** and stops after **stage 9
(echograms)**, with `--parallel-workers 1` and a post-denoise sanity clip at
{_clip_threshold(metrics)}. Strict mode is on: any
configured filter that could not run on a configured channel, any missing
category, or any failed required product aborts the arm with a non-zero exit.
A `run-manifest.json` is written **only** after the full artifact matrix
validates, so its presence certifies that the arm is comparable.

Code revision consistency across arms: **{"yes" if metrics.get("code_revision_consistent") else "NO — see Reproducibility appendix"}**.

### Pipeline stages exercised

| Stage | Operation | Product |
|---|---|---|
| 5 | Denoise: impulse ∪ attenuation ∪ transient masks, then background subtraction, then sanity clip | `--denoised.zarr` |
| 6b | Prune: drop pings above the NaN-fraction threshold, then cross-talk-flagged pings | `--pruned.zarr` |
| 7 | MVBS: 1 m × 10 s regrid | `--mvbs.zarr` |
| 8 | NASC: 10 m × 0.5 nmi integration | `--nasc.zarr` |
| 9 | Echograms from the **saved** pruned product | PNG |

Full-resolution stage masks are written to a separate `--masks.zarr` and
dropped from the data path before pruning, so they do not propagate into MVBS
or NASC.

### Deviations from Ryan et al. (2015)

These are lineage choices, not accidents, and they matter for how the results
should be read.

1. **Masks are OR-combined, not applied as a sequential cascade.** Ryan's
   Figure 2 applies the corrections in sequence, so each filter sees the output
   of the previous one. Here all three mask filters are evaluated
   independently against the same source Sv and their masks are unioned. The
   consequence is that the masks **overlap**: per-filter counts do not sum to
   the union. Every mask figure therefore reports per-filter totals, unique
   contributions, and pairwise overlaps separately; none of them stacks.

2. **The transient filter is Fielding-style, not Ryan's.** The wired
   `transient_noise_mask` is a deep-band upward-stepping detector in the
   Fielding lineage. This is a shared choice, not an oceanstream quirk: both
   oceanstream and echopype default their dedicated transient detector to
   Fielding-style methods (echopype's `detect_transient`, PR #1544). Ryan-style
   transient noise detection exists in both codebases but is not the default —
   `transient_noise_mask_ryan` in oceanstream is implemented but unwired, and
   echopype's is `mask_transient_noise`.

3. **The `percentile` parameter present in all three preset TOMLs is inert.**
   It is a Ryan-TN parameter; the Fielding detector never reads it. It is
   listed explicitly below rather than silently dropped.

4. **Parameters are adapted, not reproduced.** The "Ryan-inspired" arm uses
   Ryan's parameter *values* where they transfer, with per-frequency variants
   for channels the paper does not cover. It is not a faithful reproduction and
   is not labelled as one.

5. **Background noise is a transform, not a mask.** `remove_background_noise`
   subtracts an estimated noise floor and NaNs sub-SNR samples. It is accounted
   separately from the mask family throughout: its own finite→NaN count and its
   own Sv-change summary.

### Analysis conventions

* Sv and MVBS are averaged in **linear space** and reported in dB. NASC is
  summed in linear space.
* Every reduction carries a finite-count guard. A bin with no finite
  contributors is `null`, never `0`.
* The first vertical bin is **excluded from all integrated comparisons**: range
  bins start at 0 m while data starts at the 1.9 m transducer depth, so bin 0
  is partial and under-integrates by roughly 19% for a 10 m NASC bin.
* Per-frequency integration bands were fixed before any results were seen:
{chr(10).join(f"  * {int(k) / 1000:.0f} kHz → {v[0]:.0f}–{v[1]:.0f} m" for k, v in sorted(TOLERANCES.integration_bands_m.items(), key=lambda kv: int(kv[0])))}
* Tolerances, also fixed in advance: Sv/MVBS `atol = {TOLERANCES.sv_atol_db} dB`;
  NASC `rtol = {TOLERANCES.nasc_rtol:g}`; coordinates
  `atol = {TOLERANCES.coord_atol_deg:g}°`; `equal_nan = {TOLERANCES.equal_nan}`
  (a NaN opposite a finite value is a difference, never a match).
* All Sv figures share the fixed colour scale
  {TOLERANCES.sv_color_limits_db[0]:.0f} to {TOLERANCES.sv_color_limits_db[1]:.0f} dB;
  all difference panels use a symmetric ±{TOLERANCES.sv_diff_limit_db:.0f} dB scale.

### Resolved parameters

Generated from the parameters each channel actually consumed at run time.

{param_tables}
### TOML fields ignored at runtime

{unused}
"""


def _clip_threshold(metrics: dict) -> str:
    """The sanity-clip threshold actually recorded by the runs."""
    thresholds = {
        (filt.get("sanity_clip") or {}).get("threshold_db")
        for per_preset in metrics.get("native", {}).values()
        for entry in per_preset.values()
        for filt in (entry.get("filters") or {}).values()
    }
    thresholds.discard(None)
    if len(thresholds) == 1:
        return f"{next(iter(thresholds)):.1f} dB"
    if thresholds:
        return "differing thresholds (see the sanity-clip table)"
    return "the configured threshold"


def _parameter_tables(metrics: dict) -> str:
    blocks = []
    for category, per_preset in metrics.get("native", {}).items():
        rows = []
        for preset in PRESET_ORDER:
            entry = per_preset.get(preset)
            if not entry:
                continue
            resolved = entry.get("resolved_params") or {}
            for label, filt in (entry.get("filters") or {}).items():
                freq = filt.get("frequency_hz")
                band = filt.get("depth_band_m") or []
                methods = (resolved.get(label) or {}).get("methods") or {}
                for method in sorted(methods) or [None]:
                    block = methods.get(method) or {}
                    params = block.get("params") or {}
                    rows.append([
                        f"`{preset}`",
                        f"{freq / 1000:.0f} kHz" if freq else _short(label),
                        f"{band[0]:.0f}–{band[1]:.0f} m" if len(band) == 2 else "—",
                        f"`{method}`" if method else "—",
                        (filt.get("status") or {}).get(method, "—") if method else "—",
                        ", ".join(f"{k}={v}" for k, v in sorted(params.items())) or "—",
                    ])
        if rows:
            blocks.append(
                f"**{category.replace('_', ' ')}**\n\n"
                + _table(
                    ["Preset", "Frequency", "Integration band", "Filter",
                     "Status", "Resolved parameters"],
                    rows,
                )
            )
    return "\n".join(blocks) or "_No resolved parameters recorded._\n"


def _unused_fields_table(metrics: dict) -> str:
    seen: dict[tuple, dict] = {}
    for per_preset in metrics.get("native", {}).values():
        for preset, entry in per_preset.items():
            for finding in entry.get("unused_toml_fields") or []:
                key = (preset, finding["frequency_hz"], finding["method"], finding["key"])
                seen[key] = finding
    if not seen:
        return "_No unused TOML fields detected._\n"
    rows = [
        [f"`{preset}`", f"{int(freq) / 1000:.0f} kHz", f"`{method}`", f"`{key}`", f["reason"]]
        for (preset, freq, method, key), f in sorted(seen.items())
    ]
    return _table(["Preset", "Frequency", "Filter", "Field", "Why it is ignored"], rows)


def _results_native(metrics: dict, produced: dict, day: str) -> str:
    sections = ["\n## Results — native support\n",
                "Each preset's real end-to-end products, as a user of that "
                "preset would obtain them. No alignment or masking is applied "
                "across arms in this section.\n"]

    for category, per_preset in metrics.get("native", {}).items():
        sections.append(f"\n### {category.replace('_', ' ').title()}\n")

        rows = []
        for preset in PRESET_ORDER:
            entry = per_preset.get(preset)
            if not entry:
                continue
            counts = entry.get("ping_counts") or {}
            before = counts.get("before_prune") or counts.get("source")
            retained = entry.get("retained_pings")
            rows.append([
                f"`{preset}`",
                f"{counts.get('source') or '—':,}" if counts.get("source") else "—",
                f"{counts.get('after_nan_prune'):,}" if counts.get("after_nan_prune") else "—",
                f"{counts.get('after_crosstalk_prune'):,}" if counts.get("after_crosstalk_prune") else "—",
                f"{retained:,}" if retained else "—",
                _pct(retained / before if (retained and before) else None, ".1f"),
            ])
        sections.append("**Ping retention**\n\n" + _table(
            ["Preset", "Source", "After NaN prune", "After cross-talk prune",
             "Retained", "Retention"],
            rows,
        ))

        cov_rows = []
        for preset in PRESET_ORDER:
            entry = per_preset.get(preset)
            if not entry:
                continue
            for label, cov in (entry.get("coverage") or {}).items():
                freq = cov.get("frequency_hz")
                cov_rows.append([
                    f"`{preset}`",
                    f"{freq / 1000:.0f} kHz" if freq else _short(label),
                    f"{cov['cells_in_band']:,}",
                    f"{cov['finite_cells']:,}",
                    _pct(cov.get("finite_fraction"), ".2f"),
                ])
        sections.append("**In-band coverage after pruning**\n\n" + _table(
            ["Preset", "Channel", "Cells in band", "Finite cells", "Coverage"],
            cov_rows,
        ))

        mask_rows = []
        for preset in PRESET_ORDER:
            entry = per_preset.get(preset)
            if not entry:
                continue
            for label, filt in (entry.get("filters") or {}).items():
                per_mask = filt.get("per_mask") or {}
                unique = filt.get("unique") or {}
                union = filt.get("union") or {}
                freq = filt.get("frequency_hz")
                for name in sorted(per_mask):
                    mask_rows.append([
                        f"`{preset}`",
                        f"{freq / 1000:.0f} kHz" if freq else _short(label),
                        f"`{name}`",
                        _pct((per_mask[name] or {}).get("fraction_of_valid")),
                        _pct((unique.get(name) or {}).get("fraction_of_valid")),
                    ])
                mask_rows.append([
                    f"`{preset}`",
                    f"{freq / 1000:.0f} kHz" if freq else _short(label),
                    "**union**",
                    _pct(union.get("fraction_of_valid")),
                    "—",
                ])
        sections.append(
            "**Flagged fraction by filter** — denominator is the source-finite "
            "Sv cells inside the declared depth band. Masks overlap, so the "
            "per-filter column does not sum to the union; the unique column "
            "does.\n\n"
            + _table(["Preset", "Channel", "Filter", "Flagged", "Unique to filter"], mask_rows)
        )

        bg_rows = []
        clip_rows = []
        for preset in PRESET_ORDER:
            entry = per_preset.get(preset)
            if not entry:
                continue
            for label, filt in (entry.get("filters") or {}).items():
                freq = filt.get("frequency_hz")
                ch = f"{freq / 1000:.0f} kHz" if freq else _short(label)
                bg = filt.get("background_correction") or {}
                change = bg.get("sv_change_db") or {}
                bg_rows.append([
                    f"`{preset}`", ch,
                    f"{bg.get('n_finite_to_nan', 0):,}" if bg.get("n_finite_to_nan") is not None else "—",
                    _pct(bg.get("fraction_finite_to_nan")),
                    _fmt(change.get("median"), ".2f"),
                    f"{_fmt(change.get('p5'), '.2f')} … {_fmt(change.get('p95'), '.2f')}",
                ])
                clip = filt.get("sanity_clip") or {}
                clip_rows.append([
                    f"`{preset}`", ch,
                    _fmt(clip.get("threshold_db"), ".1f"),
                    f"{clip.get('n_newly_clipped', 0):,}" if clip.get("n_newly_clipped") is not None else "—",
                    _pct(clip.get("fraction_of_valid")),
                ])
        sections.append(
            "**Background correction (Sv transform, accounted separately from "
            "the masks)**\n\n"
            + _table(
                ["Preset", "Channel", "Finite→NaN", "Fraction", "Median ΔSv (dB)", "p5…p95 (dB)"],
                bg_rows,
            )
        )
        sections.append("**Post-denoise sanity clip**\n\n" + _table(
            ["Preset", "Channel", "Threshold (dB)", "Newly clipped", "Fraction"],
            clip_rows,
        ))

        nasc_rows = []
        for preset in PRESET_ORDER:
            entry = per_preset.get(preset)
            if not entry:
                continue
            for label, block in (entry.get("nasc") or {}).items():
                freq = block.get("frequency_hz")
                band = block.get("depth_band_m") or []
                nasc_rows.append([
                    f"`{preset}`",
                    f"{freq / 1000:.0f} kHz" if freq else _short(label),
                    f"{band[0]:.0f}–{band[1]:.0f} m" if len(band) == 2 else "—",
                    _fmt(block.get("total"), ".4g"),
                ])
        sections.append(
            "**Depth-integrated NASC (surface bin excluded)**\n\n"
            + _table(["Preset", "Channel", "Band", "Total NASC (m² nmi⁻²)"], nasc_rows)
        )

        for path in _figures_of(produced, "mask_fractions", category):
            sections.append(_figure(path, f"{category}: flagged fraction by filter, with overlaps"))
        for path in _figures_of(produced, "histogram", category):
            sections.append(_figure(path, f"{category}: Sv distribution before and after denoising"))
        for path in _figures_of(produced, "mvbs_profiles", category):
            sections.append(_figure(path, f"{category}: mean MVBS depth profile, three presets"))
        for path in _figures_of(produced, "nasc", category):
            sections.append(_figure(path, f"{category}: depth-integrated NASC, native and common support"))

    for path in produced.get("map", []):
        sections.append(_figure(
            path,
            "Track coloured by depth-integrated NASC, using the repaired "
            "per-distance-bin NASC coordinates. Pacific inset locates the study area.",
        ))

    return "\n".join(sections)


def _results_common(metrics: dict, produced: dict) -> str:
    sections = [
        "\n## Common-support comparison\n",
        "Secondary analysis. Arms are aligned by pulse category, physical "
        "frequency, `ping_time` and depth; deltas are computed only where "
        "**all three** presets have finite support. Alignment loss is stated "
        "for every comparison — cells dropped here are not "
        "\"agreement\", they are absence of shared evidence.\n",
    ]

    for category, block in metrics.get("common_support", {}).items():
        sections.append(f"\n### {category.replace('_', ' ').title()}\n")

        mvbs = block.get("mvbs") or {}
        if mvbs.get("status") == "insufficient_arms":
            sections.append("_Insufficient arms for a common-support MVBS comparison._\n")
        else:
            align = mvbs.get("alignment", {})
            sections.append(_table(
                ["Quantity", "Baseline", "Common", "Loss"],
                [
                    ["ping_time bins",
                     f"{align.get('baseline_ping_bins', 0):,}",
                     f"{align.get('common_ping_bins', 0):,}",
                     _pct(align.get("ping_bin_loss_fraction"), ".2f")],
                    ["depth bins",
                     f"{align.get('baseline_depth_bins', 0):,}",
                     f"{align.get('common_depth_bins', 0):,}",
                     "—"],
                ],
            ))
            rows = []
            for label, ch in (mvbs.get("channels") or {}).items():
                freq = ch.get("frequency_hz")
                name = f"{freq / 1000:.0f} kHz" if freq else _short(label)
                for preset, delta in (ch.get("deltas_db") or {}).items():
                    if delta.get("status") != "ok":
                        rows.append([name, f"`{preset}`", delta.get("status", "—"), "—", "—", "—", "—"])
                        continue
                    rows.append([
                        name, f"`{preset}`",
                        f"{delta['n']:,}",
                        _fmt(delta.get("median"), ".3f"),
                        f"{_fmt(delta.get('p5'), '.2f')} … {_fmt(delta.get('p95'), '.2f')}",
                        _fmt(delta.get("max_abs"), ".2f"),
                        "yes" if delta.get("within_declared_tolerance") else "no",
                    ])
            sections.append(
                f"**MVBS delta vs `{BASELINE_PRESET}` on common support**\n\n"
                + _table(
                    ["Channel", "Preset", "Cells", "Median Δ (dB)", "p5…p95 (dB)",
                     "max |Δ| (dB)", f"Within {TOLERANCES.sv_atol_db} dB"],
                    rows,
                )
            )

        nasc = block.get("nasc") or {}
        if nasc.get("status") == "insufficient_arms":
            sections.append("_Insufficient arms for a common-support NASC comparison._\n")
        else:
            rows = []
            for label, ch in (nasc.get("channels") or {}).items():
                for preset, total in (ch.get("totals") or {}).items():
                    rel = (ch.get("relative_difference") or {}).get(preset) or {}
                    rows.append([
                        _short(label), f"`{preset}`",
                        f"{ch.get('common_distance_bins', 0)}/{ch.get('baseline_distance_bins', 0)}",
                        _pct(ch.get("alignment_loss_fraction"), ".1f"),
                        _fmt(total, ".4g"),
                        "baseline" if preset == BASELINE_PRESET else _pct(rel.get("value"), ".2f"),
                    ])
            sections.append(
                f"**Depth-integrated NASC on common support (baseline `{BASELINE_PRESET}`)**\n\n"
                + _table(
                    ["Channel", "Preset", "Common bins", "Alignment loss",
                     "Total NASC (m² nmi⁻²)", "Relative difference"],
                    rows,
                )
            )

        for path in _figures_of(produced, "difference", category):
            sections.append(_figure(
                path,
                f"{category}: ΔSv vs the {BASELINE_PRESET} arm, finite overlap only. "
                "Blank cells have no shared finite support.",
            ))
        for path in _figures_of(produced, "disagreement", category):
            sections.append(_figure(
                path,
                f"{category}: categorical mask disagreement vs {BASELINE_PRESET} — "
                "baseline-only, candidate-only, or both.",
            ))

    return "\n".join(sections)


def _interpretation(metrics: dict) -> str:
    """Templated observations only. Never asserts causation or preference."""
    observations: list[str] = []

    for category, block in metrics.get("common_support", {}).items():
        mvbs = block.get("mvbs") or {}
        for label, ch in (mvbs.get("channels") or {}).items():
            freq = ch.get("frequency_hz")
            name = f"{freq / 1000:.0f} kHz" if freq else _short(label)
            for preset, delta in (ch.get("deltas_db") or {}).items():
                if delta.get("status") != "ok":
                    continue
                verdict = (
                    "within the declared tolerance"
                    if delta.get("within_declared_tolerance")
                    else "outside the declared tolerance"
                )
                observations.append(
                    f"- {category.replace('_', ' ')} / {name}: `{preset}` differs from "
                    f"`{BASELINE_PRESET}` by a median of "
                    f"{_fmt(delta.get('median'), '.3f')} dB "
                    f"(p5–p95 {_fmt(delta.get('p5'), '.2f')} to "
                    f"{_fmt(delta.get('p95'), '.2f')} dB, max |Δ| "
                    f"{_fmt(delta.get('max_abs'), '.2f')} dB) over "
                    f"{delta.get('n', 0):,} commonly-supported MVBS cells — {verdict}."
                )
            loss = ch.get("common_support_fraction_of_baseline")
            if loss is not None:
                observations.append(
                    f"- {category.replace('_', ' ')} / {name}: common support covers "
                    f"{_pct(loss, '.1f')} of the baseline's finite MVBS cells; the "
                    "remainder is not agreement but absence of shared evidence."
                )

        nasc = block.get("nasc") or {}
        for label, ch in (nasc.get("channels") or {}).items():
            for preset, rel in (ch.get("relative_difference") or {}).items():
                observations.append(
                    f"- {category.replace('_', ' ')} / {_short(label)}: integrated NASC for "
                    f"`{preset}` differs from `{BASELINE_PRESET}` by "
                    f"{_pct(rel.get('value'), '.2f')} on common support."
                )

    body = "\n".join(observations) or "_No cross-preset observations available._"

    return f"""
## Interpretation

{INTERPRETATION_SIGNOFF_MARKER}

The observations below are generated mechanically from `metrics.json`. They
state what the numbers show and nothing more. Domain interpretation — whether a
difference is acoustically meaningful, which filter is responsible, what it
implies for biomass estimates — requires manual review, and this section stays
marked DRAFT until that review is recorded.

{body}

Two structural cautions apply to every line above:

1. **One day is one day.** These differences are a sensitivity measurement on a
   single operational day under one set of sea states and one diel cycle. They
   do not generalise, and no preset is preferred on this evidence.
2. **Overlapping masks make attribution non-trivial.** Because the three mask
   filters are OR-combined rather than cascaded, a cell flagged by two filters
   cannot be attributed to either. Use the unique-contribution column, not the
   per-filter totals, when reasoning about which filter drove a change.
"""


def _conclusions(metrics: dict) -> str:
    return """
## Conclusions

Scoped strictly to what a single-day sensitivity study can support:

* The three presets were applied to a byte-identical stage-4 Sv source under a
  single code revision, so every difference reported here is attributable to
  preset configuration and to nothing else in the pipeline.
* The magnitude of preset-driven variation in MVBS and integrated NASC is
  quantified per pulse category and per physical frequency, against tolerances
  fixed before any results were seen.
* Where arms differ, the flagged-fraction, unique-contribution and overlap
  tables locate the difference in a specific filter and channel.

This study does **not** establish that any preset is more correct. Doing so
would require blinded expert annotation of noise events and a held-out
multi-day evaluation set — both deliberately out of scope here and noted as
future work.
"""


def _preset_appendices(metrics: dict, produced: dict) -> str:
    """One appendix per preset: its own source / denoised / pruned echograms."""
    sections = [
        "\n## Appendix A — Per-preset echograms\n",
        "Each triptych shows the shared source Sv (pre-denoise), that preset's "
        "denoised Sv, and the saved stage-6b pruned product that MVBS and NASC "
        "were actually computed from. All panels share the fixed colour scale, "
        "so panels are directly comparable across presets.\n",
    ]
    triptychs = produced.get("triptych", [])
    if not triptychs:
        sections.append("_No echogram triptychs were produced._\n")
        return "\n".join(sections)

    for preset in PRESET_ORDER:
        label = PRESETS_BY_KEY[preset].label if preset in PRESETS_BY_KEY else preset
        paths = [p for p in triptychs if p.endswith(f"--{preset}.png")]
        if not paths:
            continue
        sections.append(f"\n### A.{PRESET_ORDER.index(preset) + 1} `{preset}` — {label}\n")
        for path in sorted(paths):
            stem = Path(path).stem
            sections.append(_figure(path, f"{stem}: source / denoised / pruned"))
    return "\n".join(sections)


def _references() -> str:
    return """
## References

::: {#refs}
:::
"""


def _reproducibility(metrics: dict, experiment: dict, day: str) -> str:
    src_rows = [
        [cat, entry["sha256"][:32], f"{entry['n_files']:,}", f"{entry['n_bytes']:,}"]
        for cat, entry in experiment["sources"].items()
    ]
    run_rows = []
    for key, run in metrics["runs"].items():
        run_rows.append([
            f"`{key}`",
            (run.get("code_head") or "—")[:12],
            "dirty" if run.get("code_dirty") else "clean",
            (run.get("preset_toml_sha256") or "—")[:12],
            run.get("created_utc") or "—",
        ])

    env = experiment.get("environment", {})
    pkgs = env.get("packages", {})
    pkg_rows = [[f"`{k}`", v or "—"] for k, v in sorted(pkgs.items())]

    ne = metrics.get("natural_earth", {})

    return f"""
## Reproducibility appendix

### Immutable source fingerprints

Recomputed and compared after every preset arm; any change aborts the experiment.

{_table(["Category", "sha256 (truncated)", "Files", "Bytes"], src_rows)}
### Run provenance

{_table(["Preset", "Code revision", "Worktree", "TOML sha256", "Completed (UTC)"], run_rows)}
Code revision consistent across arms: **{"yes" if metrics.get("code_revision_consistent") else "NO"}**.

### Environment

Python {env.get("python", "—")} on {env.get("platform", "—")}.

{_table(["Package", "Version"], pkg_rows)}
Natural Earth cache: `{ne.get("cache_dir") or "—"}` (cartopy {ne.get("cartopy_version") or "—"}).

### Commands

```bash
# Freeze the contract and fingerprint the immutable Sv source
python experiment_contract.py fingerprint \\
    --source-root <EXPERIMENT_ROOT>/{experiment["source_container"]} \\
    --day {day} --out experiment-manifest.json

# Pilot (short pulse, one preset, measured), then the full matrix
./run-denoise-comparison-10oct.sh pilot
./run-denoise-comparison-10oct.sh run

# Metrics + report
./run-denoise-comparison-10oct.sh report
```

Absolute paths and any credentials are omitted; `<EXPERIMENT_ROOT>` is the
local experiment directory recorded in `experiment-manifest.json`.

`results.html` is self-contained and needs no TeX engine. `results.pdf` is
built with the first available engine of `tectonic`, `xelatex`, `lualatex`.
"""


# ── Entry point ────────────────────────────────────────────────────────────


def build_markdown(metrics: dict, experiment: dict, produced: dict, day: str) -> str:
    return "\n".join([
        _abstract(metrics, experiment, day),
        _background(experiment, day),
        _prior_art(),
        _methods(metrics, experiment, day),
        _results_native(metrics, produced, day),
        _results_common(metrics, produced),
        _interpretation(metrics),
        _conclusions(metrics),
        _preset_appendices(metrics, produced),
        _references(),
        _reproducibility(metrics, experiment, day),
    ])
