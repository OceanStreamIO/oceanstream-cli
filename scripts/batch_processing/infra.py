"""Azure VM provisioning and deallocation helpers.

Wraps ``az vm`` CLI commands for creating a processing VM with cloud-init,
checking status, and deallocating when the pipeline completes.

Usage from the main script::

    from infra import create_processing_vm, deallocate_vm, delete_vm

    vm_ip = create_processing_vm(config.azure_vm)
    try:
        run_pipeline(config)
    finally:
        deallocate_vm(config.azure_vm)
"""

from __future__ import annotations

import json
import logging
import subprocess
import sys
import textwrap
from pathlib import Path

from config import AzureVMConfig

logger = logging.getLogger(__name__)


def _az(args: list[str], check: bool = True) -> subprocess.CompletedProcess:
    """Run an ``az`` CLI command and return the result."""
    cmd = ["az"] + args
    logger.info("Running: %s", " ".join(cmd))
    result = subprocess.run(
        cmd, capture_output=True, text=True, check=False,
    )
    if check and result.returncode != 0:
        logger.error("az CLI error: %s", result.stderr)
        raise RuntimeError(f"az command failed: {result.stderr}")
    return result


def _build_cloud_init(cfg: AzureVMConfig) -> str:
    """Generate a cloud-init YAML that installs system packages.

    Repo cloning and pip install are done via SSH after VM is ready,
    since the repos are private and require SSH key forwarding.
    """
    return textwrap.dedent(f"""\
        #cloud-config
        package_update: true
        package_upgrade: true
        packages:
          - python3-full
          - python3-dev
          - python3-venv
          - python3-pip
          - libpq-dev
          - git
          - libhdf5-dev
          - libnetcdf-dev
          - tippecanoe
          - build-essential
          - pkg-config
        runcmd:
          - mkdir -p /home/{cfg.admin_username}/workspace
          - chown {cfg.admin_username}:{cfg.admin_username} /home/{cfg.admin_username}/workspace
          - touch /home/{cfg.admin_username}/.cloud-init-done
    """)


def create_processing_vm(cfg: AzureVMConfig) -> str:
    """Provision a single processing VM with cloud-init.

    Returns the public IP address of the created VM.
    """
    cloud_init = _build_cloud_init(cfg)
    cloud_init_file = Path("/tmp/oceanstream_cloud_init.yaml")
    cloud_init_file.write_text(cloud_init)

    logger.info(
        "Creating VM '%s' (%s) in %s/%s",
        cfg.vm_name, cfg.vm_size, cfg.resource_group, cfg.location,
    )

    result = _az([
        "vm", "create",
        "--resource-group", cfg.resource_group,
        "--location", cfg.location,
        "--name", cfg.vm_name,
        "--size", cfg.vm_size,
        "--image", cfg.image,
        "--admin-username", cfg.admin_username,
        "--ssh-key-value", str(Path(cfg.ssh_key_path).expanduser()),
        "--authentication-type", "ssh",
        "--storage-sku", "StandardSSD_LRS",
        "--custom-data", str(cloud_init_file),
        "--vnet-name", cfg.vnet_name,
        "--subnet", cfg.subnet_name,
        "--nsg", cfg.nsg_name,
        "--os-disk-size-gb", "128",
        "--os-disk-delete-option", "Delete",
        "--nic-delete-option", "Delete",
        "--public-ip-sku", "Standard",
        "--output", "json",
    ])

    vm_info = json.loads(result.stdout)
    public_ip = vm_info.get("publicIpAddress", "")
    logger.info("VM created. Public IP: %s", public_ip)
    return public_ip


def get_vm_status(cfg: AzureVMConfig) -> str:
    """Return the power state of the VM (e.g. 'running', 'deallocated')."""
    result = _az([
        "vm", "get-instance-view",
        "--resource-group", cfg.resource_group,
        "--name", cfg.vm_name,
        "--query", "instanceView.statuses[?starts_with(code, 'PowerState/')].displayStatus",
        "--output", "tsv",
    ], check=False)
    return result.stdout.strip() if result.returncode == 0 else "unknown"


def deallocate_vm(cfg: AzureVMConfig) -> None:
    """Deallocate the VM (stops billing for compute, preserves disk)."""
    logger.info("Deallocating VM '%s'", cfg.vm_name)
    _az([
        "vm", "deallocate",
        "--resource-group", cfg.resource_group,
        "--name", cfg.vm_name,
        "--no-wait",
    ], check=False)
    logger.info("Deallocation initiated for '%s'", cfg.vm_name)


def delete_vm(cfg: AzureVMConfig) -> None:
    """Delete the VM and associated resources."""
    logger.info("Deleting VM '%s'", cfg.vm_name)
    _az([
        "vm", "delete",
        "--resource-group", cfg.resource_group,
        "--name", cfg.vm_name,
        "--yes",
        "--no-wait",
    ], check=False)
    logger.info("Deletion initiated for '%s'", cfg.vm_name)


def start_vm(cfg: AzureVMConfig) -> None:
    """Start a deallocated VM."""
    logger.info("Starting VM '%s'", cfg.vm_name)
    _az([
        "vm", "start",
        "--resource-group", cfg.resource_group,
        "--name", cfg.vm_name,
    ])
    logger.info("VM '%s' started", cfg.vm_name)


def attach_data_disk(
    cfg: AzureVMConfig,
    size_gb: int = 128,
    sku: str = "StandardSSD_LRS",
) -> None:
    """Attach a new managed data disk to the VM.

    Can be run while the VM is deallocated or running.
    The disk must be formatted and mounted via SSH after first boot::

        lsblk
        sudo mkfs.ext4 /dev/sdc
        sudo mkdir -p /mnt/data
        sudo mount /dev/sdc /mnt/data
        sudo chown oceanstream:oceanstream /mnt/data
        echo '/dev/sdc /mnt/data ext4 defaults,nofail 0 2' | sudo tee -a /etc/fstab
    """
    disk_name = f"{cfg.vm_name}-data-disk"
    logger.info(
        "Attaching %d GB data disk '%s' (sku=%s) to VM '%s'",
        size_gb, disk_name, sku, cfg.vm_name,
    )
    _az([
        "vm", "disk", "attach",
        "--resource-group", cfg.resource_group,
        "--vm-name", cfg.vm_name,
        "--name", disk_name,
        "--size-gb", str(size_gb),
        "--sku", sku,
        "--new",
    ])
    logger.info("Data disk '%s' attached to '%s'", disk_name, cfg.vm_name)


def open_nsg_port(cfg: AzureVMConfig, port: int, priority: int = 1100) -> None:
    """Open a port on the VM's NSG (e.g. 8787 for Dask dashboard)."""
    _az([
        "vm", "open-port",
        "--resource-group", cfg.resource_group,
        "--name", cfg.vm_name,
        "--port", str(port),
        "--priority", str(priority),
    ], check=False)


def main():
    """Quick CLI for VM management."""
    import argparse

    parser = argparse.ArgumentParser(description="Manage Azure processing VM")
    sub = parser.add_subparsers(dest="command")

    sub.add_parser("create", help="Create processing VM")
    sub.add_parser("start", help="Start a deallocated VM")
    sub.add_parser("status", help="Check VM status")
    sub.add_parser("deallocate", help="Deallocate VM")
    sub.add_parser("delete", help="Delete VM")
    disk_parser = sub.add_parser("attach-disk", help="Attach a new data disk")
    disk_parser.add_argument("--disk-size-gb", type=int, default=128)
    disk_parser.add_argument("--disk-sku", default="StandardSSD_LRS")

    parser.add_argument("--vm-name", default="oceanstream-batch-vm")
    parser.add_argument("--vm-size", default="Standard_E16s_v5")
    parser.add_argument("--resource-group", default="ne1-saildrone1-rg")
    parser.add_argument("--location", default="northeurope")

    args = parser.parse_args()
    cfg = AzureVMConfig(
        vm_name=args.vm_name,
        vm_size=args.vm_size,
        resource_group=args.resource_group,
        location=args.location,
    )

    if args.command == "create":
        ip = create_processing_vm(cfg)
        print(f"VM created: {ip}")
    elif args.command == "start":
        start_vm(cfg)
        print(f"VM '{cfg.vm_name}' started")
    elif args.command == "status":
        print(get_vm_status(cfg))
    elif args.command == "deallocate":
        deallocate_vm(cfg)
    elif args.command == "delete":
        delete_vm(cfg)
    elif args.command == "attach-disk":
        attach_data_disk(cfg, size_gb=args.disk_size_gb, sku=args.disk_sku)
    else:
        parser.print_help()
        sys.exit(1)


if __name__ == "__main__":
    main()
