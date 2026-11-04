"""The cluster's settings: hosts, account, filesystem layout and object storage.

Nothing site-specific lives in the repository. Each value comes from the
environment first, then from a site file of ``KEY=value`` lines: the file named
by ``HPC_SITE_FILE`` (only that one), else ``scripts/hpc/site.env`` in the source
tree (gitignored), else ``~/.oceanstream/hpc/site.env``. ``scripts/hpc/site.env.example`` lists the keys.

Assumed cluster shape (docs/hpc.md): a login node that accepts ``sbatch``,
compute nodes without outbound internet, and a transfer node reachable over SSH.
"""

from __future__ import annotations

import os
import shlex
from collections.abc import Mapping
from dataclasses import dataclass, fields
from pathlib import Path

from .console import HpcError

PROJECT_DIR = Path(__file__).resolve().parents[2]

# Keys every cluster operation needs; HPC_XFER_HOST is checked where it is used.
REQUIRED = ("HPC_USER", "HPC_LOGIN_HOST", "HPC_ACCOUNT", "HPC_PROJ", "HPC_SCRATCH", "HPC_SSH_KEY")


def site_file_candidates(env: Mapping[str, str]) -> list[Path]:
    """Where a site file is looked for, in order."""
    if env.get("HPC_SITE_FILE"):
        return [Path(env["HPC_SITE_FILE"]).expanduser()]
    out = [PROJECT_DIR / "scripts" / "hpc" / "site.env"]
    out.append(Path.home() / ".oceanstream" / "hpc" / "site.env")
    return out


def read_env_file(path: Path) -> dict[str, str]:
    """``KEY=value`` lines as a shell would read them with ``set -a; . file``.

    Comments, blank lines and an ``export`` prefix are allowed; values may be
    quoted, and ``$VAR``/``${VAR}`` are expanded from the environment. Command
    substitution and other shell syntax are not evaluated.
    """
    values: dict[str, str] = {}
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[len("export "):].strip()
        key, sep, value = line.partition("=")
        if not sep or not key.replace("_", "").isalnum():
            continue
        try:
            parts = shlex.split(value, comments=True)
        except ValueError:
            parts = [value]
        # Expand $VAR and ${VAR} as the shell would (e.g. HPC_SSH_KEY=$HOME/.ssh/key).
        values[key.strip()] = os.path.expandvars(" ".join(parts))
    return values


def _first(env: Mapping[str, str], *names: str, default: str = "") -> str:
    for name in names:
        value = env.get(name, "").strip()
        if value:
            return value
    return default


@dataclass(frozen=True)
class HpcSite:
    """Settings for one cluster, with the oceanstream directory layout derived from them."""

    user: str = ""
    login_host: str = ""
    xfer_host: str = ""
    account: str = ""
    proj: str = ""
    scratch: str = ""
    ssh_key: str = ""
    known_hosts: str = ""
    qos: str = ""
    debug_qos: str = ""
    partition: str = ""
    constraint: str = ""
    mem_per_core_gb: int = 2
    mem_per_core_gb_constrained: int = 2
    cores_per_node: int = 112
    modules: str = ""
    python_minor: str = "3.12"
    xfer_streams: int = 4
    poll_interval_s: int = 30
    poll_timeout_s: int = 172800
    # Layout. Everything sits under oceanstream/ so it can share a group allocation.
    root: str = ""
    stage: str = ""
    code_dir: str = ""
    venv: str = ""
    wheelhouse: str = ""
    log_dir: str = ""
    tmp_dir: str = ""
    raw_dir: str = ""
    gps_dir: str = ""
    products_dir: str = ""
    job_tmp: str = ""
    # Object storage (the project bucket).
    s3_bucket: str = ""
    s3_endpoint: str = ""
    s3_region: str = ""
    s3_raw_prefix: str = "hpc/raw"
    s3_gps_prefix: str = "hpc/gps"
    s3_products_prefix: str = "hpc/products"

    @classmethod
    def load(cls, env: Mapping[str, str] | None = None,
             site_file: Path | None = None) -> HpcSite:
        """Settings from *env* (default: the process environment) over the site file."""
        env = dict(os.environ if env is None else env)
        files = [site_file] if site_file else site_file_candidates(env)
        merged: dict[str, str] = {}
        for path in files:
            if path and path.is_file():
                merged = read_env_file(path)
                break
        merged.update({k: v for k, v in env.items() if v.strip()})
        return cls.from_mapping(merged)

    @classmethod
    def from_mapping(cls, m: Mapping[str, str]) -> HpcSite:
        def num(name: str, default: int) -> int:
            raw = m.get(name, "").strip()
            if not raw:
                return default
            try:
                return int(raw)
            except ValueError as e:
                raise HpcError(f"{name} must be a whole number, got {raw!r}") from e

        proj, scratch = m.get("HPC_PROJ", ""), m.get("HPC_SCRATCH", "")
        root = m.get("HPC_ROOT") or (f"{proj}/oceanstream" if proj else "")
        stage = m.get("HPC_STAGE") or (f"{scratch}/oceanstream" if scratch else "")
        per_core = num("HPC_MEM_PER_CORE_GB", 2)
        endpoint = _first(m, "OCEANSTREAM_S3_ENDPOINT", "AWS_S3_ENDPOINT", "S3_ENDPOINT_URL")
        if endpoint and "://" not in endpoint:
            endpoint = f"https://{endpoint}"
        return cls(
            user=m.get("HPC_USER", ""),
            login_host=m.get("HPC_LOGIN_HOST", ""),
            xfer_host=m.get("HPC_XFER_HOST", ""),
            account=m.get("HPC_ACCOUNT", ""),
            proj=proj,
            scratch=scratch,
            ssh_key=str(Path(m["HPC_SSH_KEY"]).expanduser()) if m.get("HPC_SSH_KEY") else "",
            known_hosts=m.get("HPC_KNOWN_HOSTS", ""),
            qos=m.get("HPC_QOS", ""),
            debug_qos=m.get("HPC_DEBUG_QOS", ""),
            partition=m.get("HPC_PARTITION", ""),
            constraint=m.get("HPC_CONSTRAINT", ""),
            mem_per_core_gb=per_core,
            mem_per_core_gb_constrained=num("HPC_MEM_PER_CORE_GB_CONSTRAINED", per_core),
            cores_per_node=num("HPC_CORES_PER_NODE", 112),
            modules=m.get("HPC_MODULES", ""),
            python_minor=m.get("HPC_PYTHON_MINOR", "3.12"),
            xfer_streams=num("HPC_XFER_STREAMS", 4),
            poll_interval_s=num("HPC_POLL_INTERVAL", 30),
            poll_timeout_s=num("HPC_POLL_TIMEOUT", 172800),
            root=root,
            stage=stage,
            code_dir=m.get("HPC_CODE_DIR") or (f"{root}/code" if root else ""),
            venv=m.get("HPC_VENV") or (f"{root}/venv" if root else ""),
            wheelhouse=m.get("HPC_WHEELHOUSE") or (f"{root}/wheelhouse" if root else ""),
            log_dir=m.get("HPC_LOG_DIR") or (f"{root}/logs" if root else ""),
            tmp_dir=m.get("HPC_TMP_DIR") or (f"{root}/tmp" if root else ""),
            raw_dir=m.get("HPC_RAW_DIR") or (f"{stage}/raw" if stage else ""),
            gps_dir=m.get("HPC_GPS_DIR") or (f"{stage}/gps" if stage else ""),
            products_dir=m.get("HPC_PRODUCTS_DIR") or (f"{stage}/products" if stage else ""),
            job_tmp=m.get("HPC_JOB_TMP") or (f"{stage}/tmp" if stage else ""),
            s3_bucket=m.get("OCEANSTREAM_S3_BUCKET", ""),
            s3_endpoint=endpoint,
            s3_region=_first(m, "OCEANSTREAM_S3_REGION", "AWS_DEFAULT_REGION"),
            s3_raw_prefix=m.get("OCEANSTREAM_S3_RAW_PREFIX") or "hpc/raw",
            s3_gps_prefix=m.get("OCEANSTREAM_S3_GPS_PREFIX") or "hpc/gps",
            s3_products_prefix=m.get("OCEANSTREAM_S3_PRODUCTS_PREFIX") or "hpc/products",
        )

    def missing(self, *extra: str) -> list[str]:
        """Names of required settings that are not set."""
        by_env = {
            "HPC_USER": self.user, "HPC_LOGIN_HOST": self.login_host,
            "HPC_ACCOUNT": self.account, "HPC_PROJ": self.proj, "HPC_SCRATCH": self.scratch,
            "HPC_SSH_KEY": self.ssh_key, "HPC_XFER_HOST": self.xfer_host,
            "OCEANSTREAM_S3_BUCKET": self.s3_bucket,
        }
        return [name for name in (*REQUIRED, *extra) if not by_env.get(name)]

    def require(self, *extra: str) -> None:
        """Raise with the list of unset settings, and how to set them."""
        missing = self.missing(*extra)
        if missing:
            raise HpcError(
                f"Site settings missing: {' '.join(missing)}. Copy scripts/hpc/site.env.example "
                "to scripts/hpc/site.env and fill it in, or export the variables (docs/hpc.md)."
            )

    def require_key(self, *extra: str) -> None:
        self.require(*extra)
        if not Path(self.ssh_key).is_file():
            raise HpcError(f"No SSH key at {self.ssh_key} (HPC_SSH_KEY).")

    def s3_uri(self, key: str) -> str:
        """An object key in the project bucket as an ``s3://`` URI."""
        if not self.s3_bucket:
            raise HpcError("OCEANSTREAM_S3_BUCKET is not set.")
        return f"s3://{self.s3_bucket}/{key.lstrip('/')}"

    def mem_per_core(self, constraint: str) -> int:
        return self.mem_per_core_gb_constrained if constraint else self.mem_per_core_gb

    def as_env(self) -> dict[str, str]:
        """The settings under their environment names, for child processes."""
        names = {f.name: f.name for f in fields(self)}
        special = {
            "s3_bucket": "OCEANSTREAM_S3_BUCKET", "s3_endpoint": "OCEANSTREAM_S3_ENDPOINT",
            "s3_region": "OCEANSTREAM_S3_REGION", "s3_raw_prefix": "OCEANSTREAM_S3_RAW_PREFIX",
            "s3_gps_prefix": "OCEANSTREAM_S3_GPS_PREFIX",
            "s3_products_prefix": "OCEANSTREAM_S3_PRODUCTS_PREFIX",
            "mem_per_core_gb": "HPC_MEM_PER_CORE_GB",
            "mem_per_core_gb_constrained": "HPC_MEM_PER_CORE_GB_CONSTRAINED",
            "poll_interval_s": "HPC_POLL_INTERVAL", "poll_timeout_s": "HPC_POLL_TIMEOUT",
        }
        out = {}
        for name in names:
            value = getattr(self, name)
            if value in ("", None):
                continue
            out[special.get(name, f"HPC_{name.upper()}")] = str(value)
        return out
