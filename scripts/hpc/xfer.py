#!/usr/bin/env python3
"""Move files between object storage and the cluster, over SSH, from this side.

The site does not allow its machines to open connections to external hosts.
That includes its queued transfer suite: it reached object storage from some of
its nodes until October 2026, by accident of their network rather than by
design, and the site has since said plainly that access to external hosts is
not permitted. So every byte here moves on a connection this side opens:

    pull   object storage  ->  this process  ->  ssh  ->  cluster filesystem
    push   cluster filesystem  ->  ssh  ->  this process  ->  object storage

Nothing touches local disk. Each file is one stream: read from S3 and written
into ``ssh ... 'cat > file.part'``, or read from ``ssh ... 'cat file'`` and
uploaded in parts. Several streams run at once, each on an SSH connection of its
own -- a connection carries about 30 MB/s, and channels of one multiplexed
connection share that.

A file is never left half-written under its real name. A pull writes to
``.part``, checks the size it was promised and only then renames; a push checks
the object's size after the upload. A transfer that dies is resumed by running
the same command again: what is already there at the right size is skipped.

    xfer.py pull  --src s3://bucket/prefix/ --dest /cluster/dir
    xfer.py pull  --src s3://copernicus-dem-30m/ --files-from cells.txt --flatten \\
                  --opendata --dest /cluster/dir
    xfer.py push  --src /cluster/dir --dest s3://bucket/prefix/ --include 'xyz.stage.*'
    xfer.py push  --src /cluster/file --dest s3://bucket/key --file
    xfer.py push  --src /cluster/store.zarr --dest s3://bucket/store.zarr/ --tar --replace
    xfer.py check --src /cluster/dir --dest s3://bucket/prefix/ --include '...'

``--tar`` sends a whole tree as tar streams instead of one SSH channel per file,
which is what a Zarr store of thousands of small chunks needs. ``--replace``
makes the destination match the source inside the filters: once every transfer
has succeeded, what the destination has and the source does not is removed.

The SSH side comes from the environment the shell library sets up:
``HPC_SSH_CONFIG`` (a client config file) and ``HPC_XFER_TARGET``
(``user@host``). Object storage is the first of ``XFER_S3_ENDPOINT``,
``BASEMAP_S3_ENDPOINT``, ``OCEANSTREAM_S3_ENDPOINT``, ``AWS_S3_ENDPOINT`` and
``S3_ENDPOINT_URL`` that is set, with the usual AWS credentials; ``--opendata``
reads a public AWS bucket anonymously instead.

Only botocore is imported, not boto3, so the same file runs wherever s3fs or
boto3 is installed -- the build driver and the Prefect submitter alike.
"""

from __future__ import annotations

import argparse
import fnmatch
import os
import shlex
import subprocess
import sys
import tempfile
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import PurePosixPath

CHUNK = 8 << 20
PART = 64 << 20
ATTEMPTS = int(os.environ.get("HPC_XFER_ATTEMPTS") or 3)


# --- what to move -------------------------------------------------------------

@dataclass(frozen=True)
class Item:
    """One file: where it comes from, where it goes, and how big it is."""

    source: str
    dest: str
    size: int


def split_s3(uri: str) -> tuple[str, str]:
    if not uri.startswith("s3://"):
        raise SystemExit(f"not an s3:// URI: {uri}")
    bucket, _, key = uri[5:].partition("/")
    return bucket, key


def wanted(name: str, include: list[str], exclude: list[str]) -> bool:
    """rclone's rule, for the patterns used here: excluded if any exclude
    matches, otherwise included if there are no includes or one matches."""
    base = PurePosixPath(name).name
    if any(fnmatch.fnmatch(name, p) or fnmatch.fnmatch(base, p) for p in exclude):
        return False
    return not include or any(fnmatch.fnmatch(name, p) or fnmatch.fnmatch(base, p)
                              for p in include)


def plan(source: dict[str, int], present: dict[str, int], src_root: str, dest_root: str,
         *, flatten: bool = False, always: bool = False) -> list[Item]:
    """What still has to move: everything absent, or present at another size.

    *source* and *present* map a relative path to a size. With *flatten* the
    destination drops the source's directories, which is how the elevation
    cells -- one directory per cell in their bucket -- land in the single
    directory the pipeline reads. *always* moves everything regardless, for
    destinations whose names are reused between runs.
    """
    items = []
    for rel, size in sorted(source.items()):
        target = PurePosixPath(rel).name if flatten else rel
        if not always and present.get(target) == size:
            continue
        items.append(Item(_join(src_root, rel), _join(dest_root, target), size))
    return items


def _join(root: str, rel: str) -> str:
    if not rel:
        return root
    return root.rstrip("/") + "/" + rel


# --- the two ends -----------------------------------------------------------------

class Remote:
    """The cluster, through SSH. Each worker thread gets a connection of its own."""

    def __init__(self, config: str, target: str):
        self.config, self.target = config, target
        self._paths = tempfile.mkdtemp(prefix="xfer-ssh-")
        self._local = threading.local()

    def _args(self) -> list[str]:
        slot = getattr(self._local, "slot", None)
        if slot is None:
            slot = self._local.slot = f"{self._paths}/w{threading.get_ident()}"
        return ["ssh", "-F", self.config, "-o", "ControlMaster=auto",
                "-o", f"ControlPath={slot}", "-o", "ControlPersist=120", self.target]

    def run(self, command: str, **kw) -> subprocess.CompletedProcess:
        return subprocess.run([*self._args(), command], **kw)

    def popen(self, command: str, **kw) -> subprocess.Popen:
        return subprocess.Popen([*self._args(), command], **kw)

    def delete(self, root: str, rels: list[str]) -> None:
        """Remove *rels* under *root*: relative paths only, never above it."""
        safe = [r for r in rels if r and not r.startswith("/") and ".." not in r.split("/")]
        if len(safe) != len(rels):
            raise SystemExit("refusing to delete a path that leaves the destination")
        if not safe:
            return
        data = b"\0".join(r.encode() for r in safe) + b"\0"
        self.run(f"cd {shlex.quote(root)} && xargs -0 rm -f --", input=data, check=True,
                 capture_output=True)

    def listing(self, root: str) -> dict[str, int]:
        """Relative path -> size for every file under *root* (or *root* itself)."""
        q = shlex.quote(root)
        cmd = (f"if [ -f {q} ]; then stat -c '%s' {q} | sed 's/^/\t/'; "
               f"elif [ -d {q} ]; then find {q} -type f ! -name '*.part' -printf '%P\\t%s\\n'; fi")
        out = self.run(cmd, capture_output=True, text=True, check=True).stdout
        found = {}
        for line in out.splitlines():
            rel, _, size = line.rpartition("\t")
            if size.isdigit():
                found[rel] = int(size)
        return found


def _first_env(*names: str, default: str = "") -> str:
    for name in names:
        value = os.environ.get(name, "").strip()
        if value:
            return value
    return default


def endpoint() -> str:
    url = _first_env("XFER_S3_ENDPOINT", "BASEMAP_S3_ENDPOINT", "OCEANSTREAM_S3_ENDPOINT",
                     "AWS_S3_ENDPOINT", "S3_ENDPOINT_URL",
                     default="https://s3.waw3-1.cloudferro.com")
    # GDAL's AWS_S3_ENDPOINT is a bare host name.
    return url if "://" in url else f"https://{url}"


class Store:
    """Object storage: ours by endpoint and credentials, or a public AWS bucket."""

    def __init__(self, opendata: bool = False):
        import botocore.session
        from botocore import UNSIGNED
        from botocore.config import Config

        session = botocore.session.get_session()
        if opendata:
            self.client = session.create_client(
                "s3", region_name=os.environ.get("HPC_OPENDATA_REGION", "eu-central-1"),
                config=Config(signature_version=UNSIGNED, retries={"max_attempts": 10}))
        else:
            # Each project names its keys differently; the first one set wins,
            # and with none the usual AWS chain applies.
            key = _first_env("XFER_S3_ACCESS_KEY_ID", "ARCO_S3_ACCESS_KEY_ID",
                             "OCEANSTREAM_S3_ACCESS_KEY_ID", "AWS_S3_ACCESS_KEY_ID",
                             "AWS_ACCESS_KEY_ID") or None
            secret = _first_env("XFER_S3_SECRET_ACCESS_KEY", "ARCO_S3_SECRET_ACCESS_KEY",
                                "OCEANSTREAM_S3_SECRET_ACCESS_KEY", "AWS_S3_SECRET_ACCESS_KEY",
                                "AWS_SECRET_ACCESS_KEY") or None
            self.client = session.create_client(
                "s3", endpoint_url=endpoint(),
                region_name=_first_env("XFER_S3_REGION", "BASEMAP_S3_REGION",
                                       "OCEANSTREAM_S3_REGION", "AWS_DEFAULT_REGION",
                                       default="waw3-1"),
                aws_access_key_id=key, aws_secret_access_key=secret,
                config=Config(retries={"max_attempts": 10}))

    def listing(self, uri: str) -> dict[str, int]:
        """Relative key -> size for every object under *uri*."""
        bucket, prefix = split_s3(uri)
        found = {}
        for page in self.client.get_paginator("list_objects_v2").paginate(Bucket=bucket,
                                                                          Prefix=prefix):
            for obj in page.get("Contents", []):
                found[obj["Key"][len(prefix):].lstrip("/")] = obj["Size"]
        return found

    def size(self, uri: str) -> int | None:
        bucket, key = split_s3(uri)
        try:
            return self.client.head_object(Bucket=bucket, Key=key)["ContentLength"]
        except Exception:                                  # noqa: BLE001
            return None

    def upload(self, stream, uri: str, part: int = PART) -> None:
        """Upload a non-seekable stream, in parts once it outgrows one."""
        bucket, key = split_s3(uri)
        chunk = _read_full(stream, part)
        if len(chunk) < part:
            self.client.put_object(Bucket=bucket, Key=key, Body=chunk)
            return
        upload_id = self.client.create_multipart_upload(Bucket=bucket, Key=key)["UploadId"]
        parts = []
        try:
            number = 1
            while chunk:
                tag = self.client.upload_part(Bucket=bucket, Key=key, PartNumber=number,
                                              UploadId=upload_id, Body=chunk)["ETag"]
                parts.append({"PartNumber": number, "ETag": tag})
                number += 1
                chunk = _read_full(stream, part)
            self.client.complete_multipart_upload(Bucket=bucket, Key=key, UploadId=upload_id,
                                                  MultipartUpload={"Parts": parts})
        except BaseException:
            self.client.abort_multipart_upload(Bucket=bucket, Key=key, UploadId=upload_id)
            raise

    def delete(self, uris: list[str]) -> None:
        by_bucket: dict[str, list[str]] = {}
        for uri in uris:
            bucket, key = split_s3(uri)
            by_bucket.setdefault(bucket, []).append(key)
        for bucket, keys in by_bucket.items():
            for i in range(0, len(keys), 1000):
                self.client.delete_objects(Bucket=bucket, Delete={
                    "Objects": [{"Key": k} for k in keys[i:i + 1000]], "Quiet": True})


def _read_full(stream, size: int) -> bytes:
    """Up to *size* bytes: a pipe returns what it has, not what was asked for."""
    out = bytearray()
    while len(out) < size:
        got = stream.read(size - len(out))
        if not got:
            break
        out += got
    return bytes(out)


# --- moving one file -------------------------------------------------------------

def pull_one(store: Store, remote: Remote, item: Item) -> None:
    """S3 object -> cluster file, through a .part that is renamed only when whole."""
    bucket, key = split_s3(item.source)
    dest = shlex.quote(item.dest)
    part = shlex.quote(item.dest + ".part")
    parent = shlex.quote(str(PurePosixPath(item.dest).parent))
    receive = (f"mkdir -p {parent} && cat > {part} && "
               f"[ \"$(stat -c %s {part})\" = {item.size} ] && mv -f {part} {dest} "
               f"|| {{ rm -f {part}; exit 3; }}")
    body = store.client.get_object(Bucket=bucket, Key=key)["Body"]
    proc = remote.popen(receive, stdin=subprocess.PIPE, stderr=subprocess.PIPE)
    try:
        for chunk in iter(lambda: body.read(CHUNK), b""):
            proc.stdin.write(chunk)
        proc.stdin.close()
    except BrokenPipeError:
        pass
    finally:
        body.close()
    err = proc.stderr.read().decode(errors="replace").strip()
    if proc.wait() != 0:
        raise RuntimeError(f"{item.dest}: the cluster side failed ({proc.returncode}) {err[-300:]}")


def push_one(store: Store, remote: Remote, item: Item) -> None:
    """Cluster file -> S3 object, uploaded in parts, then checked by size."""
    proc = remote.popen(f"cat {shlex.quote(item.source)}", stdout=subprocess.PIPE,
                        stderr=subprocess.PIPE)
    try:
        store.upload(proc.stdout, item.dest)
    finally:
        proc.stdout.close()
    err = proc.stderr.read().decode(errors="replace").strip()
    if proc.wait() != 0:
        raise RuntimeError(f"{item.source}: could not be read ({proc.returncode}) {err[-300:]}")
    landed = store.size(item.dest)
    if landed != item.size:
        raise RuntimeError(f"{item.dest}: {landed} bytes in the store, {item.size} on the cluster")


def push_tar(store: Store, remote: Remote, items: list[Item], src_root: str,
             streams: int) -> int:
    """Many files as tar streams, one per SSH connection, uploaded as they arrive.

    One channel per file costs a process and a round trip each, which for a
    store of a hundred thousand small chunks is most of the transfer. Here the
    cluster tars the named files to stdout and this side uploads each member as
    it is read: small ones on a pool of uploaders, large ones streamed in parts.
    Returns the number of files that failed.
    """
    import tarfile

    if not items:
        print("  push: nothing to move", flush=True)
        return 0
    groups: list[list[Item]] = [[] for _ in range(max(1, streams))]
    weights = [0] * len(groups)
    for item in sorted(items, key=lambda i: -i.size):
        k = weights.index(min(weights))
        groups[k].append(item)
        weights[k] += item.size
    total = sum(i.size for i in items)
    print(f"  push (tar): {len(items):,} files, {total / 1e9:,.1f} GB, "
          f"{len([g for g in groups if g])} streams", flush=True)
    root = src_root.rstrip("/")
    lock = threading.Lock()
    tally = {"done": 0, "bytes": 0, "failed": 0}
    started = time.monotonic()

    def run_group(group: list[Item]) -> None:
        by_name = {i.source[len(root) + 1:]: i for i in group}
        proc = remote.popen(f"tar -C {shlex.quote(root)} -cf - --null -T -",
                            stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                            stderr=subprocess.PIPE)

        def feed() -> None:
            try:
                proc.stdin.write(b"\0".join(n.encode() for n in by_name) + b"\0")
            finally:
                proc.stdin.close()

        threading.Thread(target=feed, daemon=True).start()
        gate = threading.BoundedSemaphore(64)          # bounds the bytes held in memory
        seen: set[str] = set()

        def put(item: Item, data: bytes) -> None:
            try:
                bucket, key = split_s3(item.dest)
                for n in range(1, ATTEMPTS + 1):
                    try:
                        store.client.put_object(Bucket=bucket, Key=key, Body=data)
                        break
                    except Exception:                      # noqa: BLE001
                        if n == ATTEMPTS:
                            raise
                        time.sleep(min(5 * n, 30))
                with lock:
                    tally["done"] += 1
                    tally["bytes"] += len(data)
            finally:
                gate.release()

        with ThreadPoolExecutor(max_workers=8) as uploads:
            futures = []
            with tarfile.open(fileobj=proc.stdout, mode="r|") as tar:
                for member in tar:
                    if not member.isfile():
                        continue
                    name = member.name[2:] if member.name.startswith("./") else member.name
                    item = by_name.get(name)
                    if item is None:
                        continue
                    seen.add(name)
                    fh = tar.extractfile(member)
                    if member.size <= PART:
                        gate.acquire()
                        futures.append(uploads.submit(put, item, fh.read()))
                    else:
                        store.upload(fh, item.dest)
                        with lock:
                            tally["done"] += 1
                            tally["bytes"] += member.size
            for future in futures:
                future.result()
        err = proc.stderr.read().decode(errors="replace").strip()
        if proc.wait() != 0:
            raise RuntimeError(f"tar on the cluster failed ({proc.returncode}) {err[-300:]}")
        missing = set(by_name) - seen
        if missing:
            raise RuntimeError(f"{len(missing)} files were not in the tar stream, "
                               f"e.g. {sorted(missing)[0]}")

    with ThreadPoolExecutor(max_workers=len(groups)) as pool:
        futures = [pool.submit(run_group, g) for g in groups if g]
        for future in as_completed(futures):
            try:
                future.result()
            except Exception as exc:                       # noqa: BLE001
                tally["failed"] += 1
                print(f"    FAILED stream: {str(exc)[:300]}", flush=True)
    rate = tally["bytes"] / max(time.monotonic() - started, 1e-6) / 1e6
    print(f"    {tally['done']:,}/{len(items):,} files, {tally['bytes'] / 1e9:,.1f} GB, "
          f"{rate:,.0f} MB/s", flush=True)
    return tally["failed"] + (len(items) - tally["done"] if tally["failed"] else 0)


# --- running a batch ----------------------------------------------------------------

def run_batch(items: list[Item], move, streams: int, label: str) -> int:
    """Move every item with *streams* workers, retrying each. Returns failures."""
    if not items:
        print(f"  {label}: nothing to move", flush=True)
        return 0
    total = sum(i.size for i in items)
    print(f"  {label}: {len(items):,} files, {total / 1e9:,.1f} GB, {streams} streams",
          flush=True)
    done_bytes = 0
    done = failed = 0
    started = time.monotonic()
    lock = threading.Lock()
    last_report = [started]

    def attempt(item: Item) -> Item:
        for n in range(1, ATTEMPTS + 1):
            try:
                move(item)
                return item
            except Exception as exc:                       # noqa: BLE001
                if n == ATTEMPTS:
                    raise
                print(f"    retry {n}/{ATTEMPTS - 1} {PurePosixPath(item.dest).name}: "
                      f"{str(exc)[:200]}", flush=True)
                time.sleep(min(5 * n, 30))
        return item

    with ThreadPoolExecutor(max_workers=streams) as pool:
        futures = {pool.submit(attempt, item): item for item in items}
        for future in as_completed(futures):
            item = futures[future]
            try:
                future.result()
                with lock:
                    done += 1
                    done_bytes += item.size
            except Exception as exc:                       # noqa: BLE001
                failed += 1
                print(f"    FAILED {item.dest}: {str(exc)[:300]}", flush=True)
            now = time.monotonic()
            if now - last_report[0] > 30 or done + failed == len(items):
                last_report[0] = now
                rate = done_bytes / max(now - started, 1e-6) / 1e6
                print(f"    {done:,}/{len(items):,} files, {done_bytes / 1e9:,.1f}/"
                      f"{total / 1e9:,.1f} GB, {rate:,.0f} MB/s", flush=True)
    return failed


# --- the commands --------------------------------------------------------------------

def _remote() -> Remote:
    config, target = os.environ.get("HPC_SSH_CONFIG"), os.environ.get("HPC_XFER_TARGET")
    if not config or not target:
        raise SystemExit("HPC_SSH_CONFIG and HPC_XFER_TARGET must be set (lib.sh does this)")
    return Remote(config, target)


def cmd_pull(args) -> int:
    store, remote = Store(opendata=args.opendata), _remote()
    if args.files_from:
        wanted_keys = {line.strip() for line in open(args.files_from) if line.strip()}
        source = {k: v for k, v in store.listing(args.src).items() if k in wanted_keys}
        missing = wanted_keys - set(source)
        if missing:
            print(f"  {len(missing):,} listed files are not in {args.src}, e.g. "
                  f"{sorted(missing)[0]}", flush=True)
    else:
        source = {k: v for k, v in store.listing(args.src).items()
                  if wanted(k, args.include, args.exclude)}
    present = remote.listing(args.dest)
    items = plan(source, present, args.src, args.dest, flatten=args.flatten, always=args.always)
    if args.verify:
        print(f"  {len(items):,} of {len(source):,} files missing or a different size", flush=True)
        return 1 if items else 0
    failed = run_batch(items, lambda i: pull_one(store, remote, i), args.streams, "pull")
    if args.replace and not failed:
        if args.flatten:
            raise SystemExit("--replace and --flatten together would compare different names")
        extra = stale(present, source, args.include, args.exclude)
        if extra:
            print(f"  removing {len(extra):,} files the source no longer has", flush=True)
            remote.delete(args.dest, extra)
    return failed


def cmd_push(args) -> int:
    store, remote = Store(), _remote()
    if args.file:
        sizes = remote.listing(args.src)
        if "" not in sizes:
            raise SystemExit(f"no file at {args.src}")
        items = [Item(args.src, args.dest, sizes[""])]
        if not args.always and store.size(args.dest) == sizes[""]:
            items = []
    else:
        source = {k: v for k, v in remote.listing(args.src).items()
                  if wanted(k, args.include, args.exclude)}
        present = store.listing(args.dest)
        items = plan(source, {} if args.always else present, args.src, args.dest,
                     always=args.always)
    if args.tar and not args.file:
        failed = push_tar(store, remote, items, args.src, args.streams)
        if not failed:
            # Every file checked by size, since a tar member is not checked on its own.
            left = plan(source, store.listing(args.dest), args.src, args.dest)
            if left:
                print(f"    {len(left):,} files did not land at their size, e.g. {left[0].dest}",
                      flush=True)
                failed = len(left)
    else:
        failed = run_batch(items, lambda i: push_one(store, remote, i), args.streams, "push")
    if args.replace and not failed and not args.file:
        # What the destination holds under the same filters and the source does
        # not: a reprocessed day replaces the old one exactly. Only after every
        # upload succeeded, so a failed run never leaves a day half deleted.
        extra = stale(present, source, args.include, args.exclude)
        if extra:
            print(f"  removing {len(extra):,} objects the source no longer has", flush=True)
            store.delete([_join(args.dest, rel) for rel in extra])
    return failed


def stale(present: dict[str, int], source: dict[str, int],
          include: list[str], exclude: list[str]) -> list[str]:
    """Destination entries inside the filters that the source does not have."""
    return sorted(rel for rel in present if rel not in source and wanted(rel, include, exclude))


def cmd_check(args) -> int:
    """Every cluster file under --src has an object of the same size under --dest."""
    store, remote = Store(), _remote()
    source = {k: v for k, v in remote.listing(args.src).items()
              if wanted(k, args.include, args.exclude)}
    present = store.listing(args.dest)
    differ = plan(source, present, args.src, args.dest)
    for item in differ[:10]:
        print(f"    differs: {item.dest}", flush=True)
    print(f"  {len(source) - len(differ):,} of {len(source):,} files match", flush=True)
    return 0 if source and not differ else 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    sub = parser.add_subparsers(dest="command", required=True)
    for name in ("pull", "push", "check"):
        p = sub.add_parser(name)
        p.add_argument("--src", required=True)
        p.add_argument("--dest", required=True)
        p.add_argument("--include", action="append", default=[])
        p.add_argument("--exclude", action="append", default=[])
        p.add_argument("--streams", type=int,
                       default=int(os.environ.get("HPC_XFER_STREAMS") or 4))
        p.add_argument("--always", action="store_true",
                       help="move everything, even what is already there at the right size")
        if name == "pull":
            p.add_argument("--files-from")
            p.add_argument("--flatten", action="store_true")
            p.add_argument("--opendata", action="store_true")
            p.add_argument("--verify", action="store_true")
            p.add_argument("--replace", action="store_true",
                           help="also delete destination files inside the filters that the "
                                "source no longer has, once every pull has succeeded")
        if name == "push":
            p.add_argument("--file", action="store_true", help="--src is one file, --dest a key")
            p.add_argument("--tar", action="store_true",
                           help="send the files as tar streams: for many small files")
            p.add_argument("--replace", action="store_true",
                           help="also delete destination objects inside the filters that the "
                                "source no longer has, once every upload has succeeded")
    args = parser.parse_args(argv)
    handler = {"pull": cmd_pull, "push": cmd_push, "check": cmd_check}[args.command]
    failed = handler(args)
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
