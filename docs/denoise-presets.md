# Denoise presets

A denoise preset is the full set of parameters for the four filters the pipeline runs on Sv: impulse noise, attenuated signal, transient noise and background noise. The right values depend on the survey: the platform and its own noise, how the transducer is mounted, water depth and recorded range, where the scattering layers sit, the route, the season and the weather. A setting that suits a Saildrone in the open Pacific removes nothing useful on a buoy in a 76 m fjord. So every preset records what it was tuned for and which survey days it was checked on, next to the parameters.

The library lives in `oceanstream/echodata/presets/library/`, one TOML file per preset.

| Preset | Status | kHz | Tuned for |
|---|---|---|---|
| `ryan-inspired` | reference | 38, 120, 200, 333 | Open-ocean baseline after Ryan et al. (2015); the others derive from it |
| `tpos-sd-2023` | production | 38, 200 | Saildrone USV, deep central tropical Pacific; the published SD_TPOS2023_v03 products |
| `tropical-pacific-v3` | experimental | 38, 200 | The same survey with lower thresholds; checked on one day only |
| `munkholmen-fjord` | production | 38, 200 | Stationary buoy in a 76 m fjord; the published MUNKHOLMEN_2026 products |
| `research-vessel-open-ocean` | draft | 18, 38, 70, 120, 200 | Hull-mounted EK80 on deep-water research cruises; first run on RR2205 |
| `research-vessel-open-ocean-scaled` | draft | 18, 38, 70, 120, 200 | The same, with windows scaled to the ping interval and per-session filtering |
| `research-vessel-open-ocean-sensitive` | draft | 18, 38, 70, 120, 200 | The scaled variant with lower impulse and transient thresholds |

`oceanstream process echodata presets list` prints the current list.

## Using a preset

```bash
oceanstream process echodata presets show tpos-sd-2023        # conditions, validations, changes against its parent
python scripts/batch_processing/process_from_raw.py --preset tpos-sd-2023 ...
./scripts/hpc/submit-day.sh --cruise C --day D --preset tpos-sd-2023 -- ...
```

`--preset` sets `--denoise-config` to the library file and `--preset-key` to the key, so the run manifest records both. A preset file is an ordinary denoise config, so `--denoise-config <file>` and the Denoise Lab's TOML import also read it.

## Choosing a preset for a new survey

```bash
oceanstream process echodata presets match -f 18,38,70,120,200 \
    --platform research-vessel --regime open-ocean --depth 1000-5500
```

`match` ranks every preset out of 100: frequency coverage 40, platform 25, regime 20, water depth overlap 10, pulse mode 5, and 5 more for a preset adopted on real survey days. The score only picks a starting point. A preset is not adopted for a new survey until it has been checked on that survey's own days, as below.

```bash
oceanstream process echodata presets check tpos-sd-2023 --sv day.zarr   # does it cover every channel?
```

## The file format

```toml
[preset]
key = "munkholmen-fjord"            # equals the file name
name = "Munkholmen fjord buoy"
status = "production"               # reference | draft | experimental | production | deprecated
summary = "One line."
description = """Why the values are what they are."""
derived_from = "ryan-inspired"      # the preset it was tuned from; `show` lists the differences

[preset.applies_to]
platforms = ["buoy"]                # usv | research-vessel | fishing-vessel | buoy | mooring | glider | auv
regimes = ["fjord"]                 # fjord | coastal | shelf | slope | open-ocean
water_depth_m = [60.0, 90.0]
pulse_mode = "CW"
echosounders = ["Simrad EK80 WBT Mini"]
region = "Trondheim Fjord, Norway"
seasons = ["spring"]

[[preset.validations]]
cruise = "MUNKHOLMEN_2026"
days = ["2026-04-10/2026-04-14"]    # single days or start/end ranges
outcome = "adopted"                 # adopted | rejected | pending
notes = "What was looked at and what it showed."
reference = "where the results are"

[echodata.denoise]
methods = ["impulse", "attenuation", "transient", "background"]
use_frequency_specific = true
reference_ping_interval_s = 2.8     # optional: ping interval the ping-count windows were tuned at
session_gap_minutes = 10.0          # optional: filter each recording session on its own

[echodata.denoise.frequency_params.38000]
impulse     = { vertical_bin_size = "2m", ping_lags = [1, 2], threshold_db = 12.0 }
attenuation = { upper_limit_sl = 30.0, lower_limit_sl = 60.0, num_side_pings = 50, threshold = 10.0 }
transient   = { exclude_above = 20.0, depth_bin = 5.0, n_pings = 50, thr_dB = 15.0, maxts = -35.0 }
background  = { range_window = 30, ping_window = 50, SNR_threshold = "3.0dB", background_noise_max = "-125.0dB" }
```

Four rules come from how the pipeline reads a config, and the loader enforces them:

- **Every required parameter is written out.** The pipeline merges built-in per-frequency defaults underneath a config, so a key left out does not mean "off" or "the filter default"; it silently takes a built-in value. The loader rejects a preset with a required parameter missing. Optional parameters (`exclude_shallow_above`, `vote_k_of_n`, `post_dilate`, `ref_min`/`ref_max`) mean "off" when left out.
- **Every channel needs its own frequency section.** The pipeline does not fall back to a neighbouring frequency: a channel with no section is left out of the mask filters with only a log warning. The denoise stage now stops with an error instead, naming the missing frequencies.
- **Four background parameters matter in the batch pipeline.** Background removal follows echopype's arithmetic and reads `range_window`, `ping_window`, `SNR_threshold` and `background_noise_max`, taking absorption from the Sv data. `sound_absorption`, `depth_stat`, `depth_quantile` and the `guard_*` parameters only reach the standalone background mask, which the batch pipeline does not call. The loader accepts them but notes each one, so nobody tunes a parameter with no effect.
- **Retired keys are dropped.** `transient.percentile` belongs to a transient detector the pipeline does not use; it and a few other leftovers are dropped on load, with a note.

Two optional settings deal with how a survey pings:

- **`reference_ping_interval_s`**: window parameters count pings (`num_side_pings`, transient `n_pings`, background `ping_window`), so the same number covers very different times on different platforms. When this is set, the pipeline multiplies those windows by the reference interval over each channel's own median ping interval, never below 5 pings (10 for the background block, or the written value when that is smaller). Ping lags are not scaled; they compare neighbouring pings by definition.
- **`session_gap_minutes`**: pings further apart than this start a new recording session, and each session is filtered on its own, so no window reaches across a gap of hours. A session shorter than a window is left unflagged by that filter, with a warning.

Both apply only when set, so presets without them run exactly as before.

`Preset.params_sha256` hashes the methods and parameters (and the two settings above, when set) only, so editing a description does not change it.

## Tuning a preset for a new survey

1. **Start from the best match.** Copy it to a new file named for the survey or class of survey, set `status = "draft"` and `derived_from` to the source, and add a frequency section for every channel the survey recorded.
2. **Choose test days that span the survey's conditions.** Three to six days is usually enough if they cover what the noise depends on: transit and time on station (thrusters, winch and instruments in the beam on a research vessel), day and night (the scattering layers migrate, and the attenuation reference band has to contain them), the shallowest and deepest water, rough weather (bubble sweep-down), and any day with other active acoustics running (ADCPs, multibeam), which appear as impulse noise.
3. **Compute Sv once.** Run the test days with `--skip-denoising`, then run each candidate preset from that Sv with `--resume-stage 5 --sv-source-container <sv container>`, so differences come from the preset alone.
4. **Measure.** Add `--emit-denoise-diagnostics` to each candidate run. It writes, per day, channel and filter, the share of the water column flagged (unique and shared, since the masks overlap) and Sv percentiles at each stage. Look at the echograms side by side in the Denoise Lab.
5. **Judge.** Each mask filter should flag cells only where noise is visible in the echogram. Background removal should leave no rising noise ramp at depth and should keep the scattering layers. A filter that flags nothing on a day with visible noise is mistuned, not quiet. The result should hold across all test days.
6. **Record it.** Add a `[[preset.validations]]` entry with the days and the outcome, rejected trials included, because they explain the values that stayed. Promote the preset to `experimental` once it has passed its test days and to `production` once a published run uses it.

## Generalising to similar surveys

A preset adopted on one survey is the starting point for others like it. For a new survey, `match` it, check it on two or three of the new survey's days as above, and if it holds, add a validation entry for the new cruise to the same preset rather than copying it. Fork a new preset only when something has to change, and say why in its description. Over time each preset's validations show how far its conditions stretch.

## Lessons so far

- **The attenuation reference band must contain scatterers.** The filter compares each ping's median in the band with its neighbours. Placed below the scattering layer it sees only the receiver's noise floor, which bubbles over the transducer do not change, so it flags nothing. That is how `tropical-pacific-v3`'s predecessor failed (0.2 % flagged before, 0.0 % after moving the 38 kHz band to 900-1300 m).
- **In shallow water every reference layer must move above the seabed.** The open-ocean values put the attenuation band at 400-500 m and the transient start at 200-250 m.
- **Background removal does most of the work.** At Munkholmen the three mask filters removed well under 1 % of cells; background removal removed 84-91 % of the 200 kHz water column.
- **A noise floor that steps needs headroom.** The Munkholmen 200 kHz floor moves between about -102 and -92 dB re 1 W within minutes, so its noise ceiling sits at -85 dB.
- **Windows count pings, and ping rates differ by an order of magnitude.** TPOS Saildrones pinged every 2.8 s, while RR2205's EK80 pinged each of its five channels in turn, every 39 s per channel. A 100-ping window tuned on TPOS spans 4.7 minutes there and 65 minutes on RR2205. Record the ping interval a preset was tuned at.
- **Channels that ping in turn need their own pings.** On RR2205 each channel has data in one ping of five; the pipeline compacts each channel onto its own pings and recorded range before filtering, background estimation and pruning, and echograms plot each channel's own pings.
- **Check what looks like an event before tuning for it.** A pale band that motivated part of the TPOS tuning was a missing raw file, not attenuation.

## The Denoise Lab

The Lab is where presets are tuned interactively. Two of its limits matter here: its form exposes only 38 and 200 kHz, so five-frequency ship data cannot be tuned there yet, and it shows the background-mask parameters listed above, which have no effect on its runs. A preset tuned in the Lab is exported as TOML and added to the library with its `[preset]` metadata.
