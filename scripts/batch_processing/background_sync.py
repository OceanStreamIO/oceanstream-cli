"""Background Azure Blob sync for the batch pipeline.

Continuously syncs a local output directory to Azure Blob Storage
while the pipeline is running.  Uses ``azcopy sync`` (if available)
for efficient incremental uploads, falling back to a Python-level
threaded uploader.

Usage:
    syncer = BackgroundSync(local_dir, container, interval=60)
    syncer.start()          # non-blocking — runs in a daemon thread
    ...                     # pipeline stages here
    syncer.stop()           # final sync + cleanup
    syncer.join(timeout=60) # wait for last sweep to finish
"""

from __future__ import annotations

import logging
import os
import shutil
import subprocess
import threading
import time
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)


class BackgroundSync:
    """Daemon thread that periodically syncs local files to Azure Blob.

    Parameters
    ----------
    local_dir : Path
        Root of local output (e.g. /mnt/data/output).
    container : str
        Azure Blob container name.
    interval : int
        Seconds between sync sweeps (default 120).
    connection_string : str, optional
        Azure Storage connection string override.  Reads
        ``AZURE_STORAGE_CONNECTION_STRING`` from env if not given.
    max_upload_workers : int
        Parallel upload threads for the Python fallback path.
    """

    def __init__(
        self,
        local_dir: Path,
        container: str,
        interval: int = 120,
        connection_string: str | None = None,
        max_upload_workers: int = 8,
    ) -> None:
        self.local_dir = Path(local_dir)
        self.container = container
        self.interval = interval
        self.conn_str = connection_string or os.environ.get(
            "AZURE_STORAGE_CONNECTION_STRING", ""
        )
        self.max_upload_workers = max_upload_workers

        self._stop_event = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._use_azcopy = shutil.which("azcopy") is not None
        self._synced_files: dict[str, int] = {}  # blob_name → size at last sync
        self._total_uploaded = 0
        self._total_skipped = 0
        self._sweep_count = 0

    # ── public API ───────────────────────────────────────────────

    def start(self) -> None:
        """Start the background sync daemon thread."""
        if not self.conn_str:
            logger.warning("No Azure connection string — background sync disabled")
            return
        self._thread = threading.Thread(
            target=self._run_loop, name="bg-sync", daemon=True,
        )
        self._thread.start()
        method = "azcopy" if self._use_azcopy else "python-threaded"
        logger.info(
            "Background sync started (%s): %s → %s (every %ds)",
            method, self.local_dir, self.container, self.interval,
        )

    def stop(self) -> None:
        """Signal the sync thread to do one final sweep and exit."""
        if self._thread is None:
            return
        self._stop_event.set()
        logger.info("Background sync: final sweep requested")

    def join(self, timeout: float = 300) -> None:
        """Wait for the sync thread to finish."""
        if self._thread is not None:
            self._thread.join(timeout=timeout)
            logger.info(
                "Background sync finished: %d sweeps, %d uploaded, %d skipped",
                self._sweep_count, self._total_uploaded, self._total_skipped,
            )

    # ── internal ─────────────────────────────────────────────────

    def _run_loop(self) -> None:
        """Main loop: sync periodically until stop is requested."""
        while not self._stop_event.is_set():
            self._do_sync()
            # Sleep in small increments so we can respond to stop quickly
            for _ in range(self.interval * 2):
                if self._stop_event.is_set():
                    break
                time.sleep(0.5)
        # Final sweep after stop
        self._do_sync()

    def _do_sync(self) -> None:
        """Run one sync sweep."""
        source_dir = self.local_dir / self.container
        if not source_dir.exists():
            return

        t0 = time.time()
        self._sweep_count += 1

        if self._use_azcopy:
            self._sync_azcopy(source_dir)
        else:
            self._sync_python(source_dir)

        elapsed = time.time() - t0
        if elapsed > 2.0:
            logger.info(
                "Background sync sweep #%d: %.1fs (%d uploaded, %d skipped total)",
                self._sweep_count, elapsed, self._total_uploaded, self._total_skipped,
            )

    def _sync_azcopy(self, source_dir: Path) -> None:
        """Use azcopy sync for efficient incremental upload."""
        # Build SAS URL from connection string
        sas_url = self._build_container_url()
        if not sas_url:
            logger.warning("Could not build SAS URL — falling back to Python sync")
            self._use_azcopy = False
            self._sync_python(source_dir)
            return

        try:
            result = subprocess.run(
                [
                    "azcopy", "sync",
                    str(source_dir),
                    sas_url,
                    "--recursive",
                    "--put-md5",
                    "--log-level", "WARNING",
                ],
                capture_output=True,
                text=True,
                timeout=self.interval * 3,  # generous timeout
            )
            if result.returncode != 0:
                # Fall back to Python uploader on azcopy error
                logger.debug("azcopy sync failed (rc=%d): %s", result.returncode, result.stderr[:200])
                self._use_azcopy = False
                self._sync_python(source_dir)
        except subprocess.TimeoutExpired:
            logger.warning("azcopy sync timed out — will retry next sweep")
        except Exception as e:
            logger.warning("azcopy sync error: %s — falling back to Python", e)
            self._use_azcopy = False

    def _sync_python(self, source_dir: Path) -> None:
        """Python-level incremental upload using ThreadPoolExecutor."""
        from concurrent.futures import ThreadPoolExecutor, as_completed

        from azure.storage.blob import ContainerClient

        client = ContainerClient.from_connection_string(self.conn_str, self.container)
        try:
            client.create_container()
        except Exception:
            pass

        # Collect files that are new or changed since last sweep
        files_to_upload = []
        for root, _dirs, files in os.walk(source_dir):
            for fname in files:
                local_path = Path(root) / fname
                blob_name = str(local_path.relative_to(source_dir))
                try:
                    size = local_path.stat().st_size
                except OSError:
                    continue
                # Skip if already synced at same size
                if self._synced_files.get(blob_name) == size:
                    self._total_skipped += 1
                    continue
                files_to_upload.append((local_path, blob_name, size))

        if not files_to_upload:
            return

        def _upload_one(local_path: Path, blob_name: str, size: int) -> bool:
            try:
                with open(local_path, "rb") as f:
                    client.upload_blob(blob_name, f, overwrite=True)
                self._synced_files[blob_name] = size
                return True
            except Exception as e:
                logger.debug("Sync upload failed for %s: %s", blob_name, e)
                return False

        with ThreadPoolExecutor(max_workers=self.max_upload_workers) as pool:
            futures = {
                pool.submit(_upload_one, lp, bn, sz): bn
                for lp, bn, sz in files_to_upload
            }
            for fut in as_completed(futures):
                try:
                    if fut.result():
                        self._total_uploaded += 1
                except Exception:
                    pass

    def _build_container_url(self) -> str | None:
        """Build a container URL with SAS token from the connection string.

        azcopy needs an HTTPS endpoint, not a connection string.
        """
        try:
            from azure.storage.blob import (
                ContainerClient,
                generate_container_sas,
                ContainerSasPermissions,
            )
            from datetime import datetime, timedelta, timezone

            client = ContainerClient.from_connection_string(
                self.conn_str, self.container,
            )
            # Extract account name and key from connection string
            parts = dict(
                item.split("=", 1)
                for item in self.conn_str.split(";")
                if "=" in item
            )
            account_name = parts.get("AccountName", "")
            account_key = parts.get("AccountKey", "")

            if not account_name or not account_key:
                return None

            sas = generate_container_sas(
                account_name=account_name,
                container_name=self.container,
                account_key=account_key,
                permission=ContainerSasPermissions(
                    read=True, write=True, list=True,
                ),
                expiry=datetime.now(timezone.utc) + timedelta(hours=24),
            )
            return f"https://{account_name}.blob.core.windows.net/{self.container}?{sas}"
        except Exception as e:
            logger.debug("SAS generation failed: %s", e)
            return None
