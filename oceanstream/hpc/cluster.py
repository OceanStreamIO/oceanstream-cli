"""Set up and look after the cluster side: directories, code, venv, quota.

The oceanstream package is not installed in the cluster venv: jobs put the synced
code directory on ``PYTHONPATH``, so a code sync never needs a venv rebuild.
"""

from __future__ import annotations

import io
import os
import shlex
import subprocess
import tarfile
from datetime import UTC, datetime
from pathlib import Path

from . import transfer
from .console import HpcError, info, log, ok, warn
from .flowlib import parse_bsc_quota
from .remote import Cluster, strip_module_noise
from .site import PROJECT_DIR

# What a code sync ships, relative to the source tree.
SYNC_PATHS = ["oceanstream", "scripts/batch_processing", "pyproject.toml", "requirements-hpc.txt"]
SYNC_EXCLUDE = {"__pycache__", ".pytest_cache", "tests", "site.env"}

# Imports a usable cluster venv must have.
VERIFY_MODULES = ["numpy", "echopype", "xarray", "zarr", "dask", "distributed", "netCDF4",
                  "numcodecs", "bottleneck", "scipy", "rasterio", "geopandas", "pyarrow",
                  "matplotlib", "s3fs", "pystac", "typer", "oceanstream", "oceanstream.hpc.node",
                  "process_from_raw", "process_campaign"]


def bootstrap_dirs(cluster: Cluster, require_gb: int = 0) -> int:
    """Create the layout (safe to re-run); returns free GB on scratch."""
    s = cluster.site
    dirs = " ".join(shlex.quote(d) for d in (s.code_dir, s.wheelhouse, s.log_dir, s.tmp_dir,
                                              s.raw_dir, s.gps_dir, s.products_dir, s.job_tmp))
    log(f"Creating {s.root} and {s.stage}")
    r = cluster.login(f"set -eu; mkdir -p {dirs}; chmod 2770 {shlex.quote(s.root)} "
                      f"{shlex.quote(s.stage)} 2>/dev/null || true")
    if r.returncode != 0:
        raise HpcError(f"Could not create the layout: {r.stderr.strip()[-300:]}")
    r = cluster.login(f"df -Pk {shlex.quote(s.stage)} | awk 'NR==2 {{print $4}}'")
    avail = (r.stdout or "").strip()
    if not avail.isdigit():
        raise HpcError(f"Unexpected df output: {avail!r}")
    gb = int(avail) // 1024 // 1024
    info(f"df free on {s.stage}: {gb} GB (a group quota may be lower: oceanstream hpc quota)")
    if gb < require_gb:
        raise HpcError(f"Only {gb} GB free on {s.stage}, need {require_gb} GB")
    ok("Layout ready")
    return gb


def code_archive(root: Path = PROJECT_DIR) -> bytes:
    """A gzipped tar of the code a job needs."""
    buf = io.BytesIO()

    def keep(t: tarfile.TarInfo) -> tarfile.TarInfo | None:
        parts = set(Path(t.name).parts)
        if parts & SYNC_EXCLUDE or t.name.endswith(".pyc"):
            return None
        return t

    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        for rel in SYNC_PATHS:
            if (root / rel).exists():
                tar.add(root / rel, arcname=rel, filter=keep)
    return buf.getvalue()


def revision(root: Path = PROJECT_DIR) -> str:
    try:
        rev = subprocess.run(["git", "-C", str(root), "rev-parse", "--short", "HEAD"],
                             capture_output=True, text=True, check=True).stdout.strip()
        dirty = subprocess.run(["git", "-C", str(root), "status", "--porcelain", "--",
                                *SYNC_PATHS], capture_output=True, text=True,
                               check=False).stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        return "unknown"
    return rev + ("-dirty" if dirty else "")


def sync_code(cluster: Cluster, root: Path = PROJECT_DIR, dry_run: bool = False) -> str:
    """Ship the source to ``HPC_CODE_DIR`` (tar over SSH; no git or rsync there)."""
    if not (root / "scripts" / "batch_processing").is_dir():
        warn(f"No pipeline source at {root}: nothing to sync; the cluster keeps its revision.")
        return ""
    data = code_archive(root)
    rev = revision(root)
    if dry_run:
        with tarfile.open(fileobj=io.BytesIO(data), mode="r:gz") as tar:
            info(f"{len(tar.getnames())} entries ({len(data) / 1e6:.1f} MB) would be sent")
        return rev
    code = shlex.quote(cluster.site.code_dir)
    log(f"Syncing {rev} -> {cluster.site.login_host}:{cluster.site.code_dir}")
    r = cluster.login(f"mkdir -p {code} && tar -xzf - -C {code} && "
                      f"echo {shlex.quote(rev)} > {code}/.revision", input=data, text=False)
    if r.returncode != 0:
        raise HpcError(f"Code sync failed: {r.stderr.decode(errors='replace')[-300:]}")
    ok(f"Synced {rev}")
    return rev


def bootstrap_env(cluster: Cluster, recreate: bool = False, online: bool = False,
                  verify_only: bool = False) -> str:
    """Build the venv from the wheelhouse (or PyPI with *online*) and check imports."""
    s = cluster.site
    prelude = cluster.module_prelude()
    venv, code, tmp = (shlex.quote(p) for p in (s.venv, s.code_dir, s.tmp_dir))
    if not verify_only:
        log(f"Building venv at {s.venv} ({'online' if online else 'wheelhouse'})")
        pip_src = "" if online else f"--no-index --find-links={shlex.quote(s.wheelhouse)}"
        # Direct references (name @ git+https://...) cannot be fetched offline; the
        # wheelhouse holds a wheel built from exactly that reference.
        reqs = (f"{code}/requirements-hpc.txt" if online else f"{tmp}/requirements-offline.txt")
        script = f"""set -e
{prelude}
python3 -V 2>&1 | grep -q ' {s.python_minor}\\.' \\
    || {{ echo "ABORT: python3 is $(python3 -V 2>&1), want {s.python_minor}"; exit 3; }}
{'rm -rf ' + venv if recreate else ':'}
[ -x {venv}/bin/python ] || python3 -m venv {venv}
. {venv}/bin/activate; unset PYTHONHOME PYTHONPATH
mkdir -p {tmp}
{'' if online else f"sed -E 's/^([A-Za-z0-9_.-]+) @ .*/\\1/' {code}/requirements-hpc.txt > {reqs}"}
python -m pip install -q {pip_src} -r {reqs} 2>&1 | tail -20
echo "installed=$(python -m pip list --format=freeze 2>/dev/null | wc -l)"
"""
        r = cluster.login(script)
        info(strip_module_noise((r.stdout or "") + (r.stderr or "")))
        if r.returncode != 0:
            raise HpcError("venv build failed")
    mods = ", ".join(repr(m) for m in VERIFY_MODULES)
    check = f"""{prelude}
. {venv}/bin/activate; unset PYTHONHOME
export PYTHONPATH={code}:{code}/scripts/batch_processing
cd {code}
python - <<'PY'
import importlib, sys
print(f'python {{sys.version.split()[0]}} at {{sys.executable}}')
for m in [{mods}]:
    try:
        mod = importlib.import_module(m)
        print(f'  ok   {{m:22s}} {{getattr(mod, "__version__", "")}}')
    except Exception as e:
        print(f'  FAIL {{m:22s}} {{type(e).__name__}}: {{e}}')
PY
"""
    r = cluster.login(check)
    out = strip_module_noise((r.stdout or "") + (r.stderr or ""))
    info(out)
    if "FAIL" in out or r.returncode != 0:
        raise HpcError("Some imports failed in the cluster venv")
    ok(f"Environment ready at {s.venv}")
    return out


def quota_table(cluster: Cluster) -> str:
    """The group's ``bsc_quota`` table, from the transfer host."""
    r = cluster.xfer("bsc_quota 2>/dev/null")
    if r.returncode != 0:
        raise HpcError(f"bsc_quota failed on {cluster.site.xfer_host}")
    return str(r.stdout)


def scratch_free_gb(cluster: Cluster) -> float:
    """Group scratch headroom, from ``bsc_quota``."""
    used, quota = parse_bsc_quota(quota_table(cluster))
    return quota - used


def smoke_test(cluster: Cluster) -> bool:
    """Write a probe on scratch, push it to the bucket over SSH, check it, remove both."""
    s = cluster.site
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    probe = f"{s.job_tmp}/smoke-{stamp}.txt"
    key = f"hpc/smoke-test/smoke-{stamp}.txt"
    if not cluster.xfer_ok(f"mkdir -p {shlex.quote(s.job_tmp)} && printf 'oceanstream transfer "
                           f"probe %s\\n' {stamp} > {shlex.quote(probe)}"):
        raise HpcError("Could not write a probe on the transfer node")
    good = cluster.transfer("push", ["--file", "--src", probe, "--dest", s.s3_uri(key)]) == 0
    (ok if good else warn)(f"{'PASS' if good else 'FAIL'}: scratch -> {s.s3_bucket}/{key} over SSH")
    cluster.xfer(f"rm -f {shlex.quote(probe)}")
    try:
        with _env(cluster.transfer_env()):
            transfer.Store().delete([s.s3_uri(key)])
    except Exception as e:  # noqa: BLE001 - the probe object is harmless
        warn(f"Could not remove the probe object: {e}")
    return good


class _env:
    """Temporarily set environment variables."""

    def __init__(self, values: dict[str, str]):
        self.values = values
        self.saved: dict[str, str | None] = {}

    def __enter__(self) -> None:
        for k, v in self.values.items():
            self.saved[k] = os.environ.get(k)
            os.environ[k] = v

    def __exit__(self, *exc: object) -> None:
        for k, v in self.saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
