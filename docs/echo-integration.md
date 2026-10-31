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

**Zero is a lower bound.** A cell below the noise was measured, but its content can't be told apart from noise: it held anything from nothing up to the SNR threshold. Counting it as zero follows survey practice, where integration counts only backscatter above a threshold, and makes NASC read as "backscatter above the detection threshold". On IESSNS 2021 (2021-07-16, 07:19–09:42, seabed 70–150 m, noise capped) the alternatives were measured against zero:
- **38 kHz:** zero, the cells' own noise-subtracted values and the threshold value agree within 0.1 dB of mean water-column Sv.
- **120 kHz:** with 74 % of cells below the noise, zero (−71.8 dB) is up to 4 dB below the noise-subtracted estimate (−67.7 dB) and 8 dB below the upper bound.

A noise-subtracted second variable was considered and left out on 2026-10-23, to stay with the survey convention.

**Why the seabed loses its height.** echopype's `compute_NASC` multiplies each bin's mean sv by the summed depth steps of the samples in the bin, whether or not they hold data. A 10 m bin straddling the seabed would count 10 m of height for the water above the line. Blanking the depth of seabed cells, as the surface exclusion already does, limits the height to the water.

## Integration layer per channel

A preset can limit where each channel is integrated, in `[echodata.integration.<Hz>]` tables:

```toml
[echodata.integration.18000]
start_depth_m = 30.0       # leave out everything shallower
max_noise_sv_db = -85.0    # stop where the noise, as Sv at that range, exceeds this
```

Cells outside the layer are left out together with their height, like the seabed. Frequencies without a table are integrated over the whole range, down to the seabed.

- **Start depth.** Just below the transducers, bubbles and transducer ringing produce strong echoes that are not biology. How deep they reach depends on frequency, ship and weather. On IESSNS 2021 (transducers at 9 m), 18 kHz read −61 to −63 dB from 12 to 16 m and fell to −82 dB by 30 m. In the 10–20 m bin it held 62 % of NASC on one day and 2 % on another.
- **End, from the noise.** The per-ping noise level from background removal, plus spreading and absorption loss, gives the noise as Sv at each range. Where it approaches the weakest scattering of interest, noise samples that clear the SNR threshold by chance are integrated while those below it count as zero, so NASC rises with range. With −85 dB, the cells those samples add stay near −94 dB. On IESSNS 2021 this ends 120 kHz at about 220–300 m and 200 kHz at about 130–145 m. 18 and 38 kHz are not cut, because their noise stays below −90 dB within the 500 m range.

Each channel then covers its own water: NASC at 200 kHz and at 38 kHz describe different depth ranges. The NASC store keeps its 10 m depth bins, so comparisons across frequencies can sum over the depths they share. The layer used is stored in the `integration_layers` attribute.

## Output

- **MVBS.** A bin that was measured but held nothing above the noise is stored as −999 dB, meaning no backscatter. NaN still means no valid data. In echograms −999 dB takes the lowest colour; white remains "no data".
- **NASC.** Such bins are 0, and `NASC_log` is −inf.
- **Provenance.** Both stores carry an `integration` attribute stating these rules.

## Filling gaps in MVBS

With `--mvbs-fill-gaps`, an MVBS bin left without data only because its cells were removed as artefacts gets the mean of the bins around it. The window is `--mvbs-fill-window` (default `11x3`): 11 ping-time bins wide by 3 range bins tall, much wider than thick, because backscatter changes far more with depth than along the track. The mean is linear and covers the bins in the window that hold a value: −999 dB counts as zero, other gaps and empty bins are left out. A gap with no such bin nearby stays NaN.

Bins past a channel's range, above the surface exclusion or under the seabed are not gaps: nothing was measured there, and they stay NaN. Bins at −999 dB are measured, so they stay too. The store's `gap_filled` variable marks the filled bins, and its `gap_fill` attribute states the window. NASC is not filled: it integrates the cells, where the rules above already let a bin's other cells stand in for its artefacts. Finding the gaps bins the artefact cells a second time, so MVBS takes about twice as long with filling on.

## Pruning

Stage 6b removes pings before MVBS and NASC. A ping is dropped when it has no measured water column, or when at least `--prune-artefact-threshold` (default 0.5) of its measured water column was removed as artefacts. Cells below the noise do not count against a ping: a ping of empty water stays, because the averages need its zeros. Among channels pinging together, one good channel keeps the ping; a channel that did not transmit in a ping has no say.

Cross-talk detection then drops pings whose deep reference band (800–1200 m by default) is raised: mean Sv more than 6 dB above the day's median. It reads the Sv as measured, with artefact and seabed cells left out, because background removal leaves the band mostly empty and the mean of the few cells it keeps swings past the threshold. On SD_TPOS2023_v03 (2023-10-10, short pulse) reading the denoised Sv dropped 1,227 of 29,438 pings; reading the measured Sv dropped 64, in the noise bursts.

Stores without per-cell flags (written before stage 5 kept flags) fall back to the older rule: drop pings with at least `--prune-threshold` (0.8) NaN.

## Products made before this change

RR2205 and SD_TPOS2023_v03 were integrated by skipping the empty cells, and pruned by NaN fraction. Their Sv, denoised and masked stores are unaffected. Rerunning from stage 6 (`--resume-stage 6`) rebuilds seabed masking, pruning, MVBS, NASC and echograms from the stored denoised data.
