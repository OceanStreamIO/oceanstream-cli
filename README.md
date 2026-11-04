# oceanstream-cli

## Processing campaigns on an HPC cluster

`oceanstream campaign create <ID> --from-catalogue <cruises.csv>` describes a cruise from the raw-data catalogue (STAC metadata, web-app ingest fields, a run configuration), and `oceanstream hpc run-cruise <ID>` stages it on a Slurm cluster, processes it as one whole-node job, pushes the products to S3 and publishes them. See [docs/hpc-campaigns.md](docs/hpc-campaigns.md) and [docs/hpc.md](docs/hpc.md).
