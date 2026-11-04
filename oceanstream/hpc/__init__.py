"""Run the echodata pipeline on a Slurm cluster whose machines cannot connect out.

Raw input and products move through S3-compatible object storage. Every transfer
is opened from this side and streamed over SSH to the cluster's transfer node
(:mod:`oceanstream.hpc.transfer`), so the cluster needs no internet access and no
object-storage credentials.

Modules:

- :mod:`.site` -- the cluster's settings (hosts, account, filesystem layout)
- :mod:`.remote` -- SSH to the login and transfer hosts
- :mod:`.transfer` -- object storage <-> cluster, over SSH
- :mod:`.slurm` -- render, submit and follow batch jobs
- :mod:`.node` -- what a job runs on its compute node
- :mod:`.staging` -- raw input onto scratch, products back to S3
- :mod:`.cruise` -- a whole cruise, resumable
- :mod:`.cluster` -- code sync, venv, directories, quota
- :mod:`.flows` -- the same steps as Prefect flows

The command line is ``oceanstream hpc ...`` (or ``python -m oceanstream.hpc``
on the cluster, which avoids importing the rest of the package). See docs/hpc.md.
"""
