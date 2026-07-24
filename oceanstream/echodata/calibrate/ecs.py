"""Echoview Calibration Supplement (.ecs) handling for echopype.

Wraps echopype's ``ECSParser`` and ``ecs_ev2ep`` to produce calibration
parameter dicts suitable for ``echopype.calibrate.compute_Sv(cal_params=...)``.

Two entry points:

* :func:`parse_ecs` — parses an ECS file (path *or* in-memory text) and
  returns the env / cal / cal_BB datasets in echopype's native form.
* :func:`build_cal_params_from_ecs` — given an ``EchoData`` and one or two
  ECS sources (short pulse / long pulse), returns ``(env_params, cal_params)``
  dicts ready to pass to ``compute_Sv``. For the dual-pulse case channels
  are picked from the matching ECS based on ``detect_pulse_mode``.
"""

from __future__ import annotations

import logging
import re
import tempfile
from pathlib import Path
from typing import TYPE_CHECKING, Literal, Optional, Union

if TYPE_CHECKING:
    import xarray as xr
    from echopype.echodata import EchoData

logger = logging.getLogger(__name__)


EcsSource = Union[Path, str]
SonarType = Literal["EK60", "EK80"]


# Echoview-exported ECS files annotate source headers with the channel
# index, e.g. ``SourceCal T1 (channel 1)``. echopype's regex only accepts
# bare ``SourceCal <token>``, and source tokens must be unique across blocks.
# Real-world Saildrone files use the same token (``T1``) for every block and
# distinguish them only via the parenthesised channel number, so we rewrite
# each header using the channel number as the unique token.
_SOURCE_HEADER_RE = re.compile(
    r"^(?P<kind>SourceCal|LocalCal|FileSet)\s+(?P<src>\S+)\s*\(\s*channel\s+(?P<ch>\d+)\s*\)\s*$",
    re.IGNORECASE,
)
_SOURCE_HEADER_NO_CHAN_RE = re.compile(
    r"^(?P<kind>SourceCal|LocalCal|FileSet)\s+(?P<src>\S+)\s*\(.*\)\s*$",
    re.IGNORECASE,
)


def _normalize_ecs_text(text: str) -> str:
    """Rewrite source headers so echopype's parser can read them.

    * ``SourceCal T1 (channel 1)`` → ``SourceCal T1_C1``
    * ``SourceCal T1 (channel 2)`` → ``SourceCal T1_C2``
    * Other ``(...)`` annotations are stripped.

    The channel-suffix path keeps each block uniquely named when (as in
    Saildrone exports) the same transducer token is reused for every block.
    """
    out_lines = []
    for line in text.splitlines(keepends=True):
        stripped = line.rstrip("\r\n")
        # Only rewrite top-level headers (no leading whitespace).
        if not stripped or line.startswith((" ", "\t")):
            out_lines.append(line)
            continue
        nl = line[len(stripped):] or "\n"
        m = _SOURCE_HEADER_RE.match(stripped)
        if m:
            out_lines.append(f"{m['kind']} {m['src']}_C{m['ch']}{nl}")
            continue
        m = _SOURCE_HEADER_NO_CHAN_RE.match(stripped)
        if m:
            out_lines.append(f"{m['kind']} {m['src']}{nl}")
            continue
        out_lines.append(line)
    return "".join(out_lines)


def _coerce_to_path(source: EcsSource) -> tuple[Path, bool]:
    """Return ``(path, is_temp)``.

    Reads the source, normalises it (stripping Echoview channel hints),
    and writes the result to a tempfile. The original file on disk is
    never modified. Caller is responsible for unlinking when ``is_temp``.
    """
    if isinstance(source, Path):
        if not source.exists():
            raise FileNotFoundError(f"ECS file not found: {source}")
        text = source.read_text(encoding="utf-8-sig")
    elif isinstance(source, str):
        if "\n" not in source and Path(source).exists():
            text = Path(source).read_text(encoding="utf-8-sig")
        elif not source.strip():
            raise ValueError("ECS content is empty")
        else:
            text = source
    else:
        raise TypeError(f"Unsupported ECS source type: {type(source).__name__}")

    normalised = _normalize_ecs_text(text)
    fd = tempfile.NamedTemporaryFile(
        mode="w", suffix=".ecs", delete=False, encoding="utf-8"
    )
    fd.write(normalised)
    fd.close()
    return Path(fd.name), True


def parse_ecs(source: EcsSource, sonar_type: SonarType = "EK80") -> dict:
    """Parse an ECS file (path or content) using echopype's parser.

    Parameters
    ----------
    source
        Either a ``Path`` / path string to an ECS file on disk, or the
        full contents of an ECS file as a string.
    sonar_type
        ``"EK60"`` or ``"EK80"``. Determines which Echoview parameter
        set is mapped (EK80 includes broadband fields).

    Returns
    -------
    dict
        ``{"env": xr.Dataset, "cal": xr.Dataset, "cal_BB": xr.Dataset | None}``.
        ``cal_BB`` is ``None`` when no ``*TableWideband`` entries are present.
    """
    try:
        from echopype.calibrate.ecs import ECSParser, ecs_ev2ep
    except ImportError as exc:
        raise ImportError(
            "echopype is required to parse ECS files. Install with `pip install echopype`."
        ) from exc

    path, is_temp = _coerce_to_path(source)
    try:
        parser = ECSParser(input_file=str(path))
        try:
            parser.parse()
        except (TypeError, ValueError, AttributeError) as e:
            logger.warning("ECS parse failed (%s) — skipping calibration", e)
            return {"env": None, "cal": None, "cal_BB": None}
        ev_dict = parser.get_cal_params()
        if not ev_dict:
            logger.warning("ECS parser returned empty dict — check ECS format")
            return {"env": None, "cal": None, "cal_BB": None}
        ds_env, ds_cal, ds_cal_BB = ecs_ev2ep(ev_dict, sonar_type)
    finally:
        if is_temp:
            path.unlink(missing_ok=True)

    return {"env": ds_env, "cal": ds_cal, "cal_BB": ds_cal_BB}


def build_cal_params_from_ecs(
    echodata: "EchoData",
    ecs_short: Optional[EcsSource] = None,
    ecs_long: Optional[EcsSource] = None,
    sonar_type: SonarType = "EK80",
) -> tuple[dict, dict]:
    """Build ``env_params`` and ``cal_params`` dicts for ``compute_Sv``.

    Single-pulse usage::

        env, cal = build_cal_params_from_ecs(ed, ecs_short=text)
        ds_Sv = ep.calibrate.compute_Sv(
            ed, env_params=env, cal_params=cal,
            waveform_mode="CW", encode_mode="complex",
        )

    Dual-pulse usage (e.g. Saildrone with 38 kHz on long pulse and 200 kHz on
    short pulse for the same file)::

        env, cal = build_cal_params_from_ecs(ed, ecs_short=short_text,
                                             ecs_long=long_text)

    Per-channel pulse mode is detected via
    :func:`oceanstream.echodata.calibrate.saildrone.detect_pulse_mode`.
    Each channel's parameters are taken from the ECS matching its detected
    pulse mode.

    Parameters
    ----------
    echodata
        Loaded ``EchoData``. Used to read ``frequency_nominal`` for channel
        alignment and (in the dual-pulse case) per-channel pulse mode.
    ecs_short, ecs_long
        ECS source for short / long pulse. At least one must be provided.
    sonar_type
        Forwarded to :func:`parse_ecs`.

    Returns
    -------
    env_params, cal_params : dict
        Ready to pass to ``echopype.calibrate.compute_Sv``.
    """
    if ecs_short is None and ecs_long is None:
        raise ValueError("At least one of ecs_short or ecs_long must be provided")

    try:
        from echopype.calibrate.ecs import conform_channel_order, ecs_ds2dict
    except ImportError as exc:
        raise ImportError("echopype is required for ECS handling") from exc

    beam = echodata["Sonar/Beam_group1"]
    freq_ref = beam["frequency_nominal"]

    parsed_short = parse_ecs(ecs_short, sonar_type) if ecs_short is not None else None
    parsed_long = parse_ecs(ecs_long, sonar_type) if ecs_long is not None else None

    # Single-pulse: align one set to channel order and return
    if parsed_short is None or parsed_long is None:
        only = parsed_short if parsed_short is not None else parsed_long
        env_ds = only.get("env")
        cal_ds = only.get("cal")
        env_dict = ecs_ds2dict(conform_channel_order(env_ds, freq_ref)) if env_ds is not None else {}
        cal_dict = ecs_ds2dict(conform_channel_order(cal_ds, freq_ref)) if cal_ds is not None else {}
        return env_dict, cal_dict

    # Dual-pulse: detect per-channel pulse mode then merge the two sets
    import numpy as np
    import xarray as xr

    from oceanstream.echodata.calibrate.saildrone import detect_pulse_mode

    modes = detect_pulse_mode(echodata)
    if len(modes) != freq_ref.size:
        raise ValueError(
            f"detect_pulse_mode returned {len(modes)} modes but "
            f"echodata has {freq_ref.size} channels"
        )

    short_env = conform_channel_order(parsed_short["env"], freq_ref)
    short_cal = conform_channel_order(parsed_short["cal"], freq_ref)
    long_env = conform_channel_order(parsed_long["env"], freq_ref)
    long_cal = conform_channel_order(parsed_long["cal"], freq_ref)

    pick_short = np.array([m == "short" for m in modes])

    def _merge(ds_s: "xr.Dataset", ds_l: "xr.Dataset") -> "xr.Dataset":
        out = {}
        all_vars = set(ds_s.data_vars) | set(ds_l.data_vars)
        for var in all_vars:
            if var in ds_s and var in ds_l:
                # Both sides present: pick per channel
                vals = np.where(pick_short, ds_s[var].values, ds_l[var].values)
                out[var] = xr.DataArray(
                    vals, dims=["channel"], coords={"channel": ds_s["channel"]}
                )
            elif var in ds_s:
                out[var] = ds_s[var]
            else:
                out[var] = ds_l[var]
        return xr.Dataset(out)

    env_merged = _merge(short_env, long_env)
    cal_merged = _merge(short_cal, long_cal)
    logger.info(
        "Built dual-pulse cal_params: per-channel modes=%s", modes
    )
    return ecs_ds2dict(env_merged), ecs_ds2dict(cal_merged)
