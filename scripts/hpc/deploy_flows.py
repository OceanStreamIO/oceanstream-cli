"""Register the HPC flows with Prefect (run inside the submitter image).

    python scripts/hpc/deploy_flows.py [--pool oceanstream-hpc]
"""
import argparse
from pathlib import Path

from prefect import flow

APP = Path(__file__).resolve().parents[2]
DEPLOYMENTS = [
    ("scripts/hpc/flows.py:process_day_hpc", "process-day-hpc",
     "Process one echosounder day on the Slurm cluster and publish it (STAC, tiles)."),
    ("scripts/hpc/flows.py:publish_campaign", "publish-campaign",
     "Rebuild a campaign's STAC Collection and echodata track tiles."),
]


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--pool", default="oceanstream-hpc")
    pool = p.parse_args().pool
    for entrypoint, name, description in DEPLOYMENTS:
        flow.from_source(source=str(APP), entrypoint=entrypoint).deploy(
            name=name, work_pool_name=pool, tags=["oceanstream-hpc"],
            description=description, print_next_steps=False, ignore_warnings=True,
        )
        print(f"Deployed {entrypoint} as {name} -> {pool}")


if __name__ == "__main__":
    main()
