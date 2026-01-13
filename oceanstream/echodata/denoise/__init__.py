"""Denoising module for acoustic data processing.

Provides noise detection and removal algorithms for Sv datasets:
- Background noise (De Robertis & Higginbottom, 2007)
- Transient noise (Fielding et al.)
- Impulse noise
- Attenuated signal detection

Ported from _echodata-legacy-code/saildrone-echodata-processing/denoise/
"""

from oceanstream.echodata.denoise.denoise import (
    apply_denoising,
    build_noise_mask,
    build_full_mask,
    apply_noise_mask,
    create_multichannel_mask,
    drop_noisy_pings,
    extract_channel,
)
from oceanstream.echodata.denoise.background_noise import background_noise_mask
from oceanstream.echodata.denoise.transient_noise import transient_noise_mask
from oceanstream.echodata.denoise.impulse_noise import impulse_noise_mask
from oceanstream.echodata.denoise.attenuation import attenuation_mask

__all__ = [
    "apply_denoising",
    "build_noise_mask",
    "build_full_mask",
    "apply_noise_mask",
    "create_multichannel_mask",
    "drop_noisy_pings",
    "extract_channel",
    "background_noise_mask",
    "transient_noise_mask",
    "impulse_noise_mask",
    "attenuation_mask",
]
