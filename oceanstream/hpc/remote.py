"""SSH to the cluster's login and transfer hosts, and transfers through them.

Every command runs through one injectable *runner* (``subprocess.run`` by
default), so tests can record what would be sent without a cluster.
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
import tempfile
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any

from .console import HpcError
from .site import HpcSite

Runner = Callable[..., "subprocess.CompletedProcess[Any]"]

# Strip signed-URL parameters and anything that looks like an S3 secret from
# output that is logged: job logs on shared filesystems are group-readable.
_REDACT = [
    (re.compile(r"sig=[^&\"\s]*"), "sig=<redacted>"),
    (re.compile(r"X-Amz-Signature=[^&\"\s]*"), "X-Amz-Signature=<redacted>"),
    (re.compile(r"secret_access_key *= *.*"), "secret_access_key = <redacted>"),
]

# Lines that module systems print on every login shell.
_MODULE_NOISE = re.compile(r"^(load|unload|Loads|remove|Set INTEL|CPLUS)")


def redact(text: str) -> str:
    for pattern, replacement in _REDACT:
        text = pattern.sub(replacement, text)
    return text


def strip_module_noise(text: str) -> str:
    return "\n".join(line for line in text.splitlines() if not _MODULE_NOISE.match(line))


class Cluster:
    """The cluster as seen from this side: login host, transfer host, settings."""

    def __init__(self, site: HpcSite, runner: Runner | None = None):
        self.site = site
        self.run: Runner = runner or subprocess.run
        self._ssh_config: Path | None = None

    # -- ssh -----------------------------------------------------------------------
    def ssh_opts(self) -> list[str]:
        opts = ["-o", "BatchMode=yes", "-o", "StrictHostKeyChecking=accept-new",
                "-o", "ConnectTimeout=20", "-o", "ServerAliveInterval=30",
                "-o", "IdentitiesOnly=yes", "-i", self.site.ssh_key]
        if self.site.known_hosts:
            opts += ["-o", f"UserKnownHostsFile={self.site.known_hosts}"]
        return opts

    def _ssh(  # noqa: PLR0913
        self, host: str, command: str, *, input: str | bytes | None = None,
             check: bool = False, timeout: float | None = None,
             text: bool = True) -> subprocess.CompletedProcess[Any]:
        self.site.require_key()
        args = ["ssh", *self.ssh_opts(), f"{self.site.user}@{host}", command]
        return self.run(args, input=input, capture_output=True, text=text, check=check,
                        timeout=timeout)

    def login(self, command: str, **kw: Any) -> subprocess.CompletedProcess[Any]:
        """Run *command* on the login node. Quote anything that must expand remotely."""
        return self._ssh(self.site.login_host, command, **kw)

    def xfer(self, command: str, **kw: Any) -> subprocess.CompletedProcess[Any]:
        """Run *command* on the transfer node."""
        if not self.site.xfer_host:
            raise HpcError("HPC_XFER_HOST is not set.")
        return self._ssh(self.site.xfer_host, command, **kw)

    def login_ok(self, command: str) -> bool:
        return self.login(command).returncode == 0

    def xfer_ok(self, command: str) -> bool:
        return self.xfer(command).returncode == 0

    def module_prelude(self) -> str:
        """Shell prefix that loads the site modules. ``module`` is a shell function:
        never pipe or subshell it, or the environment change is lost."""
        load = f"module load {self.site.modules} >/dev/null 2>&1;" if self.site.modules else ""
        # Some site Python modules export PYTHONHOME/PYTHONPATH, which shadow a venv.
        return f"{load} unset PYTHONHOME PYTHONPATH;"

    # -- transfers -----------------------------------------------------------------
    def ssh_config_file(self) -> Path:
        """A client config for the transfer helper's own connections."""
        if self._ssh_config is None:
            lines = ["Host *", f"    IdentityFile {self.site.ssh_key}", "    IdentitiesOnly yes",
                     "    BatchMode yes", "    StrictHostKeyChecking accept-new",
                     "    ConnectTimeout 20", "    ServerAliveInterval 30",
                     "    ServerAliveCountMax 60"]
            if self.site.known_hosts:
                lines.append(f"    UserKnownHostsFile {self.site.known_hosts}")
            fd, name = tempfile.mkstemp(prefix="oceanstream-hpc-ssh-", suffix=".conf")
            with os.fdopen(fd, "w") as f:
                f.write("\n".join(lines) + "\n")
            self._ssh_config = Path(name)
        return self._ssh_config

    def transfer_env(self, extra: dict[str, str] | None = None,
                     opendata: bool = False) -> dict[str, str]:
        env = {**os.environ, **self.site.as_env(),
               "HPC_SSH_CONFIG": str(self.ssh_config_file()),
               "HPC_XFER_TARGET": f"{self.site.user}@{self.site.xfer_host}"}
        if opendata:
            # NCEI's bucket is in us-east-1; the open-data client defaults elsewhere.
            env.setdefault("HPC_OPENDATA_REGION", "us-east-1")
            # botocore applies AWS_ENDPOINT_URL to every client it makes, which would
            # send the open-data client to our own endpoint (NoSuchBucket).
            env.pop("AWS_ENDPOINT_URL", None)
            env.pop("AWS_ENDPOINT_URL_S3", None)
        env.update(extra or {})
        return env

    def transfer(self, action: str, args: Sequence[str], *, streams: int | None = None,
                 extra_env: dict[str, str] | None = None) -> int:
        """Run ``oceanstream.hpc.transfer <action> <args>`` in a child process.

        A child process keeps each transfer's environment (credentials, open-data
        region) to itself, so transfers can run side by side.
        """
        self.site.require_key("HPC_XFER_HOST")
        args = list(args)
        if streams and "--streams" not in args:
            args += ["--streams", str(streams)]
        env = self.transfer_env(extra_env, opendata="--opendata" in args)
        cmd = [sys.executable, "-m", "oceanstream.hpc.transfer", action, *args]
        return int(self.run(cmd, env=env, check=False).returncode)
