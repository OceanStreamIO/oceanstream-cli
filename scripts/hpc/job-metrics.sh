#!/usr/bin/env bash
# Print Slurm accounting for a finished compute job as JSON.
#
#   ./scripts/hpc/job-metrics.sh <jobid>     one object for the job
#   ./scripts/hpc/job-metrics.sh <arrayid>   for a job array: {"<task>": {...}, ...}
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=scripts/hpc/lib.sh
. "$SCRIPT_DIR/lib.sh"
JOBID="${1:?Usage: $0 <jobid>}"
case "$JOBID" in *[!0-9_]*) fail "Job id must be numeric: $JOBID" ;; esac
hpc_sacct "$JOBID" | python3 -c '
import json, sys
keys = ["job_id", "job_name", "state", "exit_code", "elapsed", "total_cpu", "alloc_cpus", "max_rss", "max_vmsize", "nodes"]
rows = [dict(zip(keys, l.rstrip("\n").split("|"))) for l in sys.stdin if l.strip()]
if not rows:
    sys.exit("no accounting rows")

def gb(v):
    try:
        return float(v.rstrip("G"))
    except ValueError:
        return 0.0

def summary(job, steps):
    out = {k: job[k] for k in keys[:7]}
    rss = [gb(s["max_rss"]) for s in steps if s["max_rss"]]
    out["max_rss_gb"] = max(rss) if rss else None
    return out

# Allocation rows have no "." in the id; steps (.batch, .extern) carry MaxRSS.
jobs = [r for r in rows if "." not in r["job_id"]]
by_job = {j["job_id"]: summary(j, [r for r in rows if r["job_id"].split(".")[0] == j["job_id"]]) for j in jobs}
tasks = {jid.split("_", 1)[1]: v for jid, v in by_job.items() if "_" in jid and "[" not in jid}
print(json.dumps(tasks if tasks else next(iter(by_job.values()))))
'
