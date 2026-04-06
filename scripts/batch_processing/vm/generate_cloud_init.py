#!/usr/bin/env python3
"""Generate a valid cloud-init YAML for the OceanStream batch processing VM.

Using Python + PyYAML guarantees correct YAML output regardless of special
characters in SSH keys, connection strings, or embedded scripts.
"""
from __future__ import annotations

import argparse
import base64
from pathlib import Path

import yaml


def build_cloud_init(
    *,
    deploy_key_path: Path,
    admin_user: str,
    storage_account: str,
    storage_key: str,
    db_host: str,
    db_port: str,
    db_name: str,
    db_user: str,
    db_password: str,
    oceanstream_repo: str,
    oceanstream_branch: str,
    echopype_repo: str,
    echopype_branch: str,
) -> dict:
    conn_str = (
        f"DefaultEndpointsProtocol=https;EndpointSuffix=core.windows.net;"
        f"AccountName={storage_account};AccountKey={storage_key};"
        f"BlobEndpoint=https://{storage_account}.blob.core.windows.net/;"
        f"FileEndpoint=https://{storage_account}.file.core.windows.net/;"
        f"QueueEndpoint=https://{storage_account}.queue.core.windows.net/;"
        f"TableEndpoint=https://{storage_account}.table.core.windows.net/"
    )

    deploy_key_b64 = base64.b64encode(deploy_key_path.read_bytes()).decode()

    home = f"/home/{admin_user}"

    env_content = (
        f"AZ_SOURCE_CONNECTION_STRING='{conn_str}'\n"
        f"AZURE_STORAGE_ACCOUNT_NAME='{storage_account}'\n"
        f"AZURE_STORAGE_ACCOUNT_KEY='{storage_key}'\n"
        "CONVERTED_CONTAINER_NAME='converted'\n"
        "PROCESSED_CONTAINER_NAME='processed'\n"
        f"DB_NAME='{db_name}'\n"
        f"DB_HOST='{db_host}'\n"
        f"DB_PASSWORD='{db_password}'\n"
        f"DB_PORT='{db_port}'\n"
        f"DB_USER='{db_user}'\n"
        f"AZURE_STORAGE_CONNECTION_STRING='{conn_str}'\n"
    )

    install_script = f"""#!/bin/bash
set -euo pipefail
exec > {home}/workspace/install.log 2>&1
echo "=== $(date) Starting installation ==="
cd {home}/workspace
source venv/bin/activate

echo "=== Installing echopype fork ==="
cd echopype
pip install -e .
cd ..

echo "=== Installing oceanstream [echodata] ==="
cd sd-data-ingest
pip install -e ".[echodata]"
cd ..

echo "=== Installing additional deps ==="
pip install adlfs azure-storage-blob azure-storage-file-share aiohttp python-dotenv rasterio cmocean

echo "=== Verifying installations ==="
python3 -c "
import echopype; print(f'echopype {{echopype.__version__}}')
import zarr; print(f'zarr {{zarr.__version__}}')
import dask; print(f'dask {{dask.__version__}}')
import xarray; print(f'xarray {{xarray.__version__}}')
import distributed; print(f'distributed {{distributed.__version__}}')
try:
    import oceanstream; print('oceanstream OK')
except ImportError as e:
    print(f'oceanstream import: {{e}}')
print('All imports verified!')
"

echo "=== $(date) Installation complete ==="
touch {home}/workspace/.install-done
"""

    download_calibration_script = f"""#!/bin/bash
set -euo pipefail
source {home}/workspace/venv/bin/activate
python3 << 'PYEOF'
from azure.storage.fileshare import ShareServiceClient
conn = "{conn_str}"
svc = ShareServiceClient.from_connection_string(conn)
share = svc.get_share_client("saildroneraw")
fc = share.get_directory_client("calibration").get_file_client("calibration_values.xlsx")
data = fc.download_file().readall()
out = "{home}/workspace/calibration/calibration_values.xlsx"
with open(out, "wb") as f:
    f.write(data)
print(f"Downloaded calibration file: {{len(data)}} bytes -> {{out}}")
PYEOF
"""

    ssh_config = (
        "Host github.com\n"
        f"  IdentityFile {home}/.ssh/deploy_key\n"
        "  IdentitiesOnly yes\n"
        "  StrictHostKeyChecking accept-new\n"
    )

    bashrc_snippet = (
        "source ~/workspace/venv/bin/activate 2>/dev/null || true\n"
        "cd ~/workspace/sd-data-ingest/scripts/batch_processing 2>/dev/null || true\n"
    )

    return {
        "package_update": True,
        "package_upgrade": True,
        "packages": [
            "python3.12",
            "python3.12-venv",
            "python3.12-dev",
            "python3-pip",
            "git",
            "build-essential",
            "libhdf5-dev",
            "libnetcdf-dev",
            "tmux",
            "htop",
            "jq",
        ],
        "write_files": [
            {
                "path": f"{home}/.ssh/deploy_key",
                "encoding": "b64",
                "content": deploy_key_b64,
                "owner": f"{admin_user}:{admin_user}",
                "permissions": "0600",
                "defer": True,
            },
            {
                "path": f"{home}/.ssh/config",
                "content": ssh_config,
                "owner": f"{admin_user}:{admin_user}",
                "permissions": "0644",
                "defer": True,
            },
            {
                "path": f"{home}/workspace/sd-data-ingest/.env",
                "content": env_content,
                "owner": f"{admin_user}:{admin_user}",
                "permissions": "0600",
                "defer": True,
            },
            {
                "path": f"{home}/workspace/install.sh",
                "content": install_script,
                "owner": f"{admin_user}:{admin_user}",
                "permissions": "0755",
                "defer": True,
            },
            {
                "path": f"{home}/workspace/download-calibration.sh",
                "content": download_calibration_script,
                "owner": f"{admin_user}:{admin_user}",
                "permissions": "0755",
                "defer": True,
            },
            {
                "path": f"{home}/.bashrc.d/oceanstream.sh",
                "content": bashrc_snippet,
                "owner": f"{admin_user}:{admin_user}",
                "permissions": "0644",
                "defer": True,
            },
        ],
        "runcmd": [
            # SSH setup
            f"mkdir -p {home}/.ssh",
            f"chmod 700 {home}/.ssh",
            f"ssh-keyscan -H github.com >> {home}/.ssh/known_hosts 2>/dev/null",
            f"chmod 644 {home}/.ssh/known_hosts",
            f"chown -R {admin_user}:{admin_user} {home}/.ssh",

            # Data disk — find the 256GB attached disk (NVMe on v6+, SCSI on v5)
            ["bash", "-c",
             "DISK=$(lsblk -dno NAME,SIZE,TYPE | awk '/disk/ && /256G/{print \"/dev/\"$1}' | head -1); "
             "if [ -n \"$DISK\" ]; then "
             "parted $DISK --script mklabel gpt mkpart primary ext4 0% 100% && sleep 2 && "
             "mkfs.ext4 -L datadisk ${DISK}p1 && "
             "mkdir -p /mnt/data && "
             "mount ${DISK}p1 /mnt/data && "
             "echo \"${DISK}p1 /mnt/data ext4 defaults,nofail,discard 0 2\" >> /etc/fstab && "
             "echo \"Data disk $DISK formatted and mounted\"; "
             "else echo \"WARNING: 256GB data disk not found\"; fi"],
            f"chown -R {admin_user}:{admin_user} /mnt/data",
            "mkdir -p /mnt/data/raw_cache /mnt/data/output",
            f"chown -R {admin_user}:{admin_user} /mnt/data",

            # Clone repos
            f"mkdir -p {home}/workspace",
            f"chown {admin_user}:{admin_user} {home}/workspace",
            (
                f"sudo -u {admin_user} git clone"
                f" --branch {oceanstream_branch} --single-branch"
                f" {oceanstream_repo} {home}/workspace/sd-data-ingest"
            ),
            (
                f"sudo -u {admin_user} git clone"
                f" --branch {echopype_branch} --single-branch"
                f" {echopype_repo} {home}/workspace/echopype"
            ),

            # Copy .env to batch subdir (write_files creates the root copy)
            (
                f"cp {home}/workspace/sd-data-ingest/.env"
                f" {home}/workspace/sd-data-ingest/scripts/batch_processing/.env"
            ),
            (
                f"chown {admin_user}:{admin_user}"
                f" {home}/workspace/sd-data-ingest/scripts/batch_processing/.env"
            ),
            f"chmod 600 {home}/workspace/sd-data-ingest/scripts/batch_processing/.env",

            # Python venv + install
            f"sudo -u {admin_user} python3.12 -m venv {home}/workspace/venv",
            f"sudo -u {admin_user} bash {home}/workspace/install.sh",

            # Calibration file
            f"mkdir -p {home}/workspace/calibration",
            f"chown {admin_user}:{admin_user} {home}/workspace/calibration",
            (
                f"sudo -u {admin_user} bash {home}/workspace/download-calibration.sh"
                " || echo 'WARNING: calibration download failed — copy manually'"
            ),

            # Bashrc
            f"mkdir -p {home}/.bashrc.d",
            f"chown {admin_user}:{admin_user} {home}/.bashrc.d",
            (
                f'grep -q "bashrc.d" {home}/.bashrc'
                f' || echo \'for f in ~/.bashrc.d/*.sh; do [ -r "$f" ] && source "$f"; done\''
                f" >> {home}/.bashrc"
            ),

            # Final — fix ownership (write_files defer may create parent dirs as root)
            f"chown -R {admin_user}:{admin_user} {home}",
            f"touch {home}/workspace/.cloud-init-done",
        ],
    }


def main():
    parser = argparse.ArgumentParser(description="Generate cloud-init YAML")
    parser.add_argument("--deploy-key", required=True)
    parser.add_argument("--admin-user", required=True)
    parser.add_argument("--storage-account", required=True)
    parser.add_argument("--storage-key", required=True)
    parser.add_argument("--db-host", required=True)
    parser.add_argument("--db-port", required=True)
    parser.add_argument("--db-name", required=True)
    parser.add_argument("--db-user", required=True)
    parser.add_argument("--db-password", required=True)
    parser.add_argument("--oceanstream-repo", required=True)
    parser.add_argument("--oceanstream-branch", required=True)
    parser.add_argument("--echopype-repo", required=True)
    parser.add_argument("--echopype-branch", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()

    data = build_cloud_init(
        deploy_key_path=Path(args.deploy_key),
        admin_user=args.admin_user,
        storage_account=args.storage_account,
        storage_key=args.storage_key,
        db_host=args.db_host,
        db_port=args.db_port,
        db_name=args.db_name,
        db_user=args.db_user,
        db_password=args.db_password,
        oceanstream_repo=args.oceanstream_repo,
        oceanstream_branch=args.oceanstream_branch,
        echopype_repo=args.echopype_repo,
        echopype_branch=args.echopype_branch,
    )

    out = Path(args.output)
    with out.open("w") as f:
        f.write("#cloud-config\n")
        yaml.dump(data, f, default_flow_style=False, sort_keys=False, width=200)

    # Validate round-trip
    yaml.safe_load(out.read_text())
    print(f"YAML validation: OK ({out})")


if __name__ == "__main__":
    main()
