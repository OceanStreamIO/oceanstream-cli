#!/usr/bin/env python3
"""Compare two runs of the same processed day, store by store.

For every Zarr store present in the reference day directory it reports:
dims/dtypes, whether coordinates are identical, and for each float data
variable the NaN-mask mismatch count and the max absolute difference
(streamed with dask, so full-resolution Sv never has to fit in memory).

Exit status 0 when every store matches within --atol, 1 otherwise.

Usage:
    python scripts/hpc/compare_products.py \\
        --ref s3://bucket/denoise-lab/run-a/2023-10-10 \\
        --new s3://bucket/hpc/parity/SD_X/2023-10-10 [--atol 1e-6] [--json out.json]
"""

from __future__ import annotations

import argparse
import json
import os
import sys


def _so() -> dict:
    ep = os.environ.get("AWS_S3_ENDPOINT") or os.environ.get("S3_ENDPOINT_URL") or ""
    if ep and not ep.startswith(("http://", "https://")):
        ep = f"https://{ep}"
    return {"endpoint_url": ep} if ep else {}


def _stores(fs, root: str) -> list[str]:
    return sorted(p.rstrip("/").rsplit("/", 1)[-1] for p in fs.ls(root, detail=False) if p.rstrip("/").endswith(".zarr"))


def compare_store(fs_a, path_a, fs_b, path_b, atol: float) -> dict:
    import numpy as np
    import xarray as xr

    a = xr.open_zarr(fs_a.get_mapper(path_a), consolidated=None)
    b = xr.open_zarr(fs_b.get_mapper(path_b), consolidated=None)
    out: dict = {"dims_ref": dict(a.sizes), "dims_new": dict(b.sizes), "vars": {}, "ok": True}
    if dict(a.sizes) != dict(b.sizes):
        out["ok"] = False
        out["error"] = "dimension mismatch"
        return out

    coords_equal = {}
    for c in a.coords:
        if c in b.coords:
            coords_equal[c] = bool(a[c].equals(b[c]))
    out["coords_equal"] = coords_equal
    if not all(coords_equal.values()):
        out["ok"] = False

    for v in a.data_vars:
        if v not in b.data_vars:
            out["vars"][v] = {"missing_in_new": True}
            out["ok"] = False
            continue
        va, vb = a[v], b[v]
        rec: dict = {"dtype_ref": str(va.dtype), "dtype_new": str(vb.dtype)}
        if va.dtype != vb.dtype or va.shape != vb.shape:
            rec["ok"] = False
        elif np.issubdtype(va.dtype, np.floating):
            da, db = va.data, vb.data
            if not hasattr(da, "dask"):
                import dask.array as dsa
                da, db = dsa.from_array(np.asarray(da)), dsa.from_array(np.asarray(db))
            import dask.array as dsa
            nan_a, nan_b = dsa.isnan(da), dsa.isnan(db)
            mismatch = (nan_a != nan_b).sum()
            diff = dsa.where(nan_a | nan_b, 0.0, abs(da - db)).max()
            n = da.size
            nan_frac = nan_a.sum() / n
            m, d, nf = dsa.compute(mismatch, diff, nan_frac)
            rec.update(nan_mismatch=int(m), max_abs_diff=float(d), nan_fraction_ref=float(nf))
            rec["ok"] = int(m) == 0 and float(d) <= atol
        else:
            rec["ok"] = bool(va.equals(vb))
        out["vars"][v] = rec
        out["ok"] = out["ok"] and rec["ok"]
    return out


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--ref", required=True)
    p.add_argument("--new", required=True)
    p.add_argument("--atol", type=float, default=1e-6)
    p.add_argument("--json", default="")
    args = p.parse_args()

    import dask
    import fsspec

    dask.config.set(scheduler="threads", num_workers=int(os.environ.get("COMPARE_THREADS", "4")))
    fs_a, ra = fsspec.core.url_to_fs(args.ref, **(_so() if args.ref.startswith("s3://") else {}))
    fs_b, rb = fsspec.core.url_to_fs(args.new, **(_so() if args.new.startswith("s3://") else {}))

    ref_stores, new_stores = _stores(fs_a, ra), _stores(fs_b, rb)
    report = {"ref": args.ref, "new": args.new, "atol": args.atol,
              "only_in_ref": sorted(set(ref_stores) - set(new_stores)),
              "only_in_new": sorted(set(new_stores) - set(ref_stores)), "stores": {}}
    all_ok = not report["only_in_ref"]
    for s in ref_stores:
        if s not in new_stores:
            continue
        print(f"== {s}", flush=True)
        r = compare_store(fs_a, f"{ra.rstrip('/')}/{s}", fs_b, f"{rb.rstrip('/')}/{s}", args.atol)
        report["stores"][s] = r
        all_ok = all_ok and r["ok"]
        for v, rec in r.get("vars", {}).items():
            if "max_abs_diff" in rec or not rec.get("ok", True):
                print(f"   {v:28s} ok={rec.get('ok')} nan_mismatch={rec.get('nan_mismatch')} "
                      f"max_abs_diff={rec.get('max_abs_diff')} nan_frac={rec.get('nan_fraction_ref')}", flush=True)
        if r.get("coords_equal") and not all(r["coords_equal"].values()):
            print(f"   coords differ: {[c for c, e in r['coords_equal'].items() if not e]}", flush=True)
        print(f"   => {'MATCH' if r['ok'] else 'DIFFERENT'}", flush=True)

    report["ok"] = all_ok
    if args.json:
        with fsspec.open(args.json, "w", **(_so() if args.json.startswith("s3://") else {})) as f:
            json.dump(report, f, indent=2, default=str)
    print("only in ref:", report["only_in_ref"], "only in new:", report["only_in_new"])
    print("OVERALL:", "MATCH" if all_ok else "DIFFERENT")
    return 0 if all_ok else 1


if __name__ == "__main__":
    sys.exit(main())
