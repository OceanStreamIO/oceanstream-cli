# Echo integration of denoised Sv

MVBS (stage 7) and NASC (stage 8) average Sv over bins of depth and time or distance. After denoising, a cell can be empty for different reasons, and an average is only right if each reason counts the right way. The rules are in `oceanstream/echodata/products.py` (`integration_view`). They apply to the input of `compute_mvbs_day` and `compute_nasc_day` in `scripts/batch_processing/process_campaign.py`, and since 2026-10-23 they are used for every cruise.

## What each empty cell counts as

| Why the cell is empty | Flag in `denoise_flags` | In MVBS and NASC |
|---|---|---|
| Below the noise (background removal, SNR threshold) | `background` | zero: it was measured and held nothing above the noise |
| Noise artefact: impulse, attenuation, transient, clip, or unknown (`other`, `legacy`) | 1, 2, 4, 16, 64, 128 | left out: no valid measurement, so the bin's other cells stand in for it |
| At or below the seabed | `seabed` | left out, together with its height |
| Above the surface exclusion (`--surface-exclusion-depth`) | none (cut by depth) | left out, together with its height |

A cell with both an artefact bit and the background bit counts as an artefact.

**Why zero matters.** An average that skips the empty cells covers only the cells that kept signal. If 5 of 50 cells in a 10 m bin hold a layer at −60 dB and the other 45 are below the noise, skipping gives −60 dB, as if the layer filled the bin. Counting the 45 as zero gives −70 dB, and NASC ten times smaller. The overstatement equals all cells divided by kept cells, so it grows with the noise level and with range rather than with the biology. On IESSNS 2021 (2021-07-16, a weak two-hour window at 38 kHz) the difference was 13.6 dB.

**Why the seabed loses its height.** echopype's `compute_NASC` multiplies each bin's mean sv by the summed depth steps of the samples in the bin, whether or not they hold data. A 10 m bin straddling the seabed would count 10 m of height for the water above the line. Blanking the depth of seabed cells, as the surface exclusion already does, limits the height to the water.

## Output

- **MVBS.** A bin that was measured but held nothing above the noise is stored as −999 dB, meaning no backscatter. NaN still means no valid data. In echograms −999 dB takes the lowest colour; white remains "no data".
- **NASC.** Such bins are 0, and `NASC_log` is −inf.
- **Provenance.** Both stores carry an `integration` attribute stating these rules.

## Pruning

Stage 6b removes pings before MVBS and NASC. A ping is dropped when it has no measured water column, or when at least `--prune-artefact-threshold` (default 0.5) of its measured water column was removed as artefacts. Cells below the noise do not count against a ping: a ping of empty water stays, because the averages need its zeros. Among channels pinging together, one good channel keeps the ping; a channel that did not transmit in a ping has no say.

Stores without per-cell flags (written before stage 5 kept flags) fall back to the older rule: drop pings with at least `--prune-threshold` (0.8) NaN.

## Products made before this change

RR2205 and SD_TPOS2023_v03 were integrated by skipping the empty cells, and pruned by NaN fraction. Their Sv, denoised and masked stores are unaffected. Rerunning from stage 6 (`--resume-stage 6`) rebuilds seabed masking, pruning, MVBS, NASC and echograms from the stored denoised data.
