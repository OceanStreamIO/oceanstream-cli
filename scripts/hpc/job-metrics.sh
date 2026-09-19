#!/usr/bin/env bash
# Print Slurm accounting for a finished compute job as one JSON object.
# Usage: ./scripts/hpc/job-metrics.sh <jobid>
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=scripts/hpc/lib.sh
. "$SCRIPT_DIR/lib.sh"
JOBID="${1:?Usage: $0 <jobid>}"
case "$JOBID" in *[!0-9_]*) fail "Job id must be numeric: $JOBID" ;; esac
hpc_sacct "$JOBID" | python3 -c '
import json, sys
rows = [l.rstrip("\n").split("|") for l in sys.stdin if l.strip()]
keys = ["job_id", "job_name", "state", "exit_code", "elapsed", "total_cpu", "alloc_cpus", "max_rss", "max_vmsize", "nodes"]
main = next((r for r in rows if "." not in r[0]), None)
if main is None:
    sys.exit("no accounting rows")
out = dict(zip(keys, main))
rss = [r[7] for r in rows if r[7]]
def gb(v):
    try: return float(v.rstrip("G"))
    except ValueError: return 0.0
out["max_rss_gb"] = max((gb(v) for v in rss), default=None)
out.pop("max_rss"); out.pop("max_vmsize"); out.pop("nodes")
print(json.dumps(out))
'
