"""A cluster that records commands instead of running them."""

from __future__ import annotations

import subprocess
from collections.abc import Callable
from typing import Any

import pytest

from oceanstream.hpc.remote import Cluster
from oceanstream.hpc.site import HpcSite

SITE = {
    "HPC_USER": "u1", "HPC_LOGIN_HOST": "login.example", "HPC_XFER_HOST": "xfer.example",
    "HPC_ACCOUNT": "acct1", "HPC_PROJ": "/proj/g", "HPC_SCRATCH": "/scratch/g",
    "HPC_SSH_KEY": "", "HPC_QOS": "normal", "HPC_PARTITION": "gpp",
    "HPC_CONSTRAINT": "highmem", "HPC_MEM_PER_CORE_GB": "2",
    "HPC_MEM_PER_CORE_GB_CONSTRAINED": "8", "HPC_MODULES": "hdf5 python/3.12.1",
    "OCEANSTREAM_S3_BUCKET": "bucket", "OCEANSTREAM_S3_ENDPOINT": "s3.example",
}


class FakeCluster(Cluster):
    """Answers SSH commands through *handler*; records transfers."""

    def __init__(self, site: HpcSite,
                 handler: Callable[[str, str], tuple[int, str]] | None = None,
                 transfer_rc: int = 0):
        super().__init__(site)
        self.handler = handler or (lambda host, cmd: (0, ""))
        self.commands: list[tuple[str, str, Any]] = []
        self.transfers: list[tuple[str, list[str]]] = []
        self.transfer_rc = transfer_rc

    def _ssh(self, host: str, command: str, *, input: Any = None, check: bool = False,
             timeout: float | None = None, text: bool = True) -> subprocess.CompletedProcess[Any]:
        self.commands.append((host, command, input))
        rc, out = self.handler(host, command)
        return subprocess.CompletedProcess(["ssh", host, command], rc, out, "")

    def transfer(self, action: str, args: Any, *, streams: int | None = None,
                 extra_env: dict[str, str] | None = None) -> int:
        self.transfers.append((action, list(args)))
        return self.transfer_rc


@pytest.fixture
def site(tmp_path: Any) -> HpcSite:
    key = tmp_path / "id_test"
    key.write_text("not a key")
    return HpcSite.from_mapping({**SITE, "HPC_SSH_KEY": str(key)})


@pytest.fixture
def fake(site: HpcSite) -> FakeCluster:
    return FakeCluster(site)
