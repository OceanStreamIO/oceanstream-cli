"""Compare two runs of the same processed day, store by store.

For every Zarr store in the reference day directory: dims and dtypes, whether
the coordinates are identical, and for each float variable the NaN-mask
mismatch count and the largest absolute difference. The arrays are streamed
with dask, so full-resolution Sv never has to fit in memory.
"""

from __future__ import annotations

import json
import os
from typing import Any

from oceanstream.stac.publish import storage_options


def _stores(fs: Any, root: str) -> list[str]:
    return sorted(p.rstrip("/").rsplit("/", 1)[-1] for p in fs.ls(root, detail=False)
                  if p.rstrip("/").endswith(".zarr"))


def compare_variable(va: Any, vb: Any, atol: float) -> dict[str, Any]:
    import dask.array as dsa  # noqa: PLC0415 - optional dependency
    import numpy as np  # noqa: PLC0415

    rec: dict[str, Any] = {"dtype_ref": str(va.dtype), "dtype_new": str(vb.dtype)}
    if va.dtype != vb.dtype or va.shape != vb.shape:
        rec["ok"] = False
    elif np.issubdtype(va.dtype, np.floating):
        da, db = va.data, vb.data
        if not hasattr(da, "dask"):
            da, db = dsa.from_array(np.asarray(da)), dsa.from_array(np.asarray(db))
        nan_a, nan_b = dsa.isnan(da), dsa.isnan(db)
        mismatch = (nan_a != nan_b).sum()
        diff = dsa.where(nan_a | nan_b, 0.0, abs(da - db)).max()
        nan_frac = nan_a.sum() / da.size
        m, d, nf = dsa.compute(mismatch, diff, nan_frac)
        rec.update(nan_mismatch=int(m), max_abs_diff=float(d), nan_fraction_ref=float(nf))
        rec["ok"] = int(m) == 0 and float(d) <= atol
    else:
        rec["ok"] = bool(va.equals(vb))
    return rec


def compare_store(fs_a: Any, path_a: str, fs_b: Any, path_b: str, atol: float
                  ) -> dict[str, Any]:
    import xarray as xr  # noqa: PLC0415 - optional dependency

    a = xr.open_zarr(fs_a.get_mapper(path_a), consolidated=None)
    b = xr.open_zarr(fs_b.get_mapper(path_b), consolidated=None)
    out: dict[str, Any] = {"dims_ref": dict(a.sizes), "dims_new": dict(b.sizes), "vars": {},
                           "ok": True}
    if dict(a.sizes) != dict(b.sizes):
        return {**out, "ok": False, "error": "dimension mismatch"}
    out["coords_equal"] = {c: bool(a[c].equals(b[c])) for c in a.coords if c in b.coords}
    out["ok"] = all(out["coords_equal"].values())
    for v in a.data_vars:
        if v not in b.data_vars:
            out["vars"][v] = {"missing_in_new": True, "ok": False}
            out["ok"] = False
            continue
        rec = compare_variable(a[v], b[v], atol)
        out["vars"][v] = rec
        out["ok"] = out["ok"] and rec["ok"]
    return out


def compare_days(ref: str, new: str, atol: float = 1e-6, report_json: str = "",
                 echo: Any = print) -> dict[str, Any]:
    """Compare the day directories *ref* and *new* (local paths or URLs)."""
    import dask  # noqa: PLC0415 - optional dependency
    import fsspec  # noqa: PLC0415

    dask.config.set(scheduler="threads", num_workers=int(os.environ.get("COMPARE_THREADS", "4")))
    fs_a, ra = fsspec.core.url_to_fs(ref, **storage_options(ref))
    fs_b, rb = fsspec.core.url_to_fs(new, **storage_options(new))
    ref_stores, new_stores = _stores(fs_a, ra), _stores(fs_b, rb)
    report: dict[str, Any] = {
        "ref": ref, "new": new, "atol": atol,
        "only_in_ref": sorted(set(ref_stores) - set(new_stores)),
        "only_in_new": sorted(set(new_stores) - set(ref_stores)), "stores": {}}
    all_ok = not report["only_in_ref"]
    for s in ref_stores:
        if s not in new_stores:
            continue
        echo(f"== {s}")
        r = compare_store(fs_a, f"{ra.rstrip('/')}/{s}", fs_b, f"{rb.rstrip('/')}/{s}", atol)
        report["stores"][s] = r
        all_ok = all_ok and r["ok"]
        for v, rec in r.get("vars", {}).items():
            if "max_abs_diff" in rec or not rec.get("ok", True):
                echo(f"   {v:28s} ok={rec.get('ok')} nan_mismatch={rec.get('nan_mismatch')} "
                     f"max_abs_diff={rec.get('max_abs_diff')} "
                     f"nan_frac={rec.get('nan_fraction_ref')}")
        if r.get("coords_equal") and not all(r["coords_equal"].values()):
            echo(f"   coords differ: {[c for c, e in r['coords_equal'].items() if not e]}")
        echo(f"   => {'MATCH' if r['ok'] else 'DIFFERENT'}")
    report["ok"] = all_ok
    if report_json:
        with fsspec.open(report_json, "w", **storage_options(report_json)) as f:
            json.dump(report, f, indent=2, default=str)
    echo(f"only in ref: {report['only_in_ref']} only in new: {report['only_in_new']}")
    echo(f"OVERALL: {'MATCH' if all_ok else 'DIFFERENT'}")
    return report
