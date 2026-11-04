"""Register the HPC flows with Prefect (run inside the submitter image).

    python -m oceanstream.hpc.deploy [--pool oceanstream-hpc]

Prefect loads each deployment's entrypoint by file path, so the flows are
re-exported here with absolute imports; ``oceanstream.hpc.flows`` itself uses
relative ones.
"""

from __future__ import annotations

import argparse

from prefect import flow

from oceanstream.hpc.flows import (  # noqa: F401 - entrypoints, loaded by Prefect
    process_batch_hpc,
    process_campaign_hpc,
    process_day_hpc,
    publish_campaign,
)
from oceanstream.hpc.site import PROJECT_DIR

ENTRY = "oceanstream/hpc/deploy.py"
DEPLOYMENTS = [
    ("process_day_hpc", "process-day-hpc",
     "Process one echosounder day on the Slurm cluster and publish it (STAC, tiles)."),
    ("process_batch_hpc", "process-batch-hpc",
     "Process many days as one Slurm job array, then publish (STAC, tiles) and import into "
     "the web app."),
    ("process_campaign_hpc", "process-campaign-hpc",
     "Process a whole campaign as a sequence of job-array batches, one batch on scratch at "
     "a time."),
    ("publish_campaign", "publish-campaign",
     "Rebuild a campaign's STAC Collection and echodata track tiles."),
]


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--pool", default="oceanstream-hpc")
    pool = p.parse_args().pool
    for function, name, description in DEPLOYMENTS:
        flow.from_source(source=str(PROJECT_DIR), entrypoint=f"{ENTRY}:{function}").deploy(
            name=name, work_pool_name=pool, tags=["oceanstream-hpc"],
            description=description, print_next_steps=False, ignore_warnings=True,
        )
        print(f"Deployed {function} as {name} -> {pool}")


if __name__ == "__main__":
    main()
