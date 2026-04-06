#!/bin/bash
# ─────────────────────────────────────────────────────────────────────────
# Provision an Azure VM for OceanStream batch processing.
#
# Creates a Standard_E48ds_v5 (48 vCPU, 384 GB RAM) instance in the
# existing ne1-saildrone1-rg resource group / VNet.  A Python helper
# generates the cloud-init YAML (avoids heredoc/quoting issues with
# embedded secrets and multi-line scripts).
#
# Usage:
#   bash provision-batch-vm.sh              # full provision
#   bash provision-batch-vm.sh --dry-run    # generate cloud-init only
#
# Prerequisites:
#   - Azure CLI logged in (az login)
#   - SSH public key at ~/.ssh/id_rsa.pub
#   - Deploy key at the saildrone project path (or set DEPLOY_KEY_FILE)
# ─────────────────────────────────────────────────────────────────────────
set -euo pipefail

# ── Resolve script dir & load secrets from .env ────────────────────────
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ENV_FILE="${SCRIPT_DIR}/.env"

if [[ ! -f "$ENV_FILE" ]]; then
    echo "ERROR: .env file not found at: $ENV_FILE"
    echo "Copy .env.example to .env and fill in your secrets."
    exit 1
fi

# shellcheck source=.env
set -a
source "$ENV_FILE"
set +a

# ── Configuration ───────────────────────────────────────────────────────
LOCATION="northeurope"
RESOURCE_GROUP="ne1-saildrone1-rg"
VNET_NAME="ne1saildronedaskvnet"
SUBNET_NAME="SchedulerSubnet"
NSG_NAME="ne1daskschedulervmnsg"

VM_NAME="oceanstream-batch-spot"
VM_SIZE="Standard_E48ds_v6"       # 48 vCPU, 384 GB RAM
ADMIN_USERNAME="oceanstream"
SSH_KEY_PATH="~/.ssh/id_rsa.pub"

DATA_DISK_SIZE_GB=256
DATA_DISK_SKU="Premium_LRS"

# ── Validate required .env variables ───────────────────────────────────
for var in AZURE_STORAGE_ACCOUNT AZURE_STORAGE_KEY DB_HOST DB_PORT DB_NAME DB_USER DB_PASSWORD DEPLOY_KEY_FILE; do
    if [[ -z "${!var:-}" ]]; then
        echo "ERROR: $var is not set. Check your .env file."
        exit 1
    fi
done

# Resolve relative DEPLOY_KEY_FILE paths from SCRIPT_DIR
if [[ "$DEPLOY_KEY_FILE" != /* ]]; then
    DEPLOY_KEY_FILE="${SCRIPT_DIR}/${DEPLOY_KEY_FILE}"
fi

if [[ ! -f "$DEPLOY_KEY_FILE" ]]; then
    echo "ERROR: deploy_key not found at: $DEPLOY_KEY_FILE"
    echo "Set DEPLOY_KEY_FILE env var to override."
    exit 1
fi
echo "Using deploy key: $(realpath "$DEPLOY_KEY_FILE")"

# ── Generate cloud-init via Python (handles YAML escaping properly) ────
CLOUD_INIT_FILE="${SCRIPT_DIR}/cloud-init-batch.yaml"

python3 "${SCRIPT_DIR}/generate_cloud_init.py" \
    --deploy-key "$DEPLOY_KEY_FILE" \
    --admin-user "$ADMIN_USERNAME" \
    --storage-account "$AZURE_STORAGE_ACCOUNT" \
    --storage-key "$AZURE_STORAGE_KEY" \
    --db-host "$DB_HOST" \
    --db-port "$DB_PORT" \
    --db-name "$DB_NAME" \
    --db-user "$DB_USER" \
    --db-password "$DB_PASSWORD" \
    --oceanstream-repo "$OCEANSTREAM_REPO" \
    --oceanstream-branch "$OCEANSTREAM_BRANCH" \
    --echopype-repo "$ECHOPYPE_REPO" \
    --echopype-branch "$ECHOPYPE_BRANCH" \
    --output "$CLOUD_INIT_FILE"

echo "Generated: ${CLOUD_INIT_FILE} ($(wc -c < "$CLOUD_INIT_FILE") bytes)"

# ── Dry-run mode ────────────────────────────────────────────────────────
if [[ "${1:-}" == "--dry-run" ]]; then
    echo ""
    echo "Dry-run complete. Review: ${CLOUD_INIT_FILE}"
    exit 0
fi

# ── Create networking resources ─────────────────────────────────────────
IP_NAME="${VM_NAME}-pip"
NIC_NAME="${VM_NAME}-nic"

echo ""
echo "=== Creating Public IP ==="
az network public-ip create \
    --resource-group "$RESOURCE_GROUP" \
    --name "$IP_NAME" \
    --sku Standard \
    --allocation-method Static \
    --location "$LOCATION" \
    -o none

echo "=== Creating Network Interface ==="
az network nic create \
    --resource-group "$RESOURCE_GROUP" \
    --name "$NIC_NAME" \
    --vnet-name "$VNET_NAME" \
    --subnet "$SUBNET_NAME" \
    --location "$LOCATION" \
    --network-security-group "$NSG_NAME" \
    --public-ip-address "$IP_NAME" \
    --accelerated-networking true \
    -o none

# ── Create VM ──────────────────────────────────────────────────────────
echo "=== Creating VM: ${VM_NAME} (${VM_SIZE}) ==="
az vm create \
    --resource-group "$RESOURCE_GROUP" \
    --location "$LOCATION" \
    --name "$VM_NAME" \
    --size "$VM_SIZE" \
    --image "Canonical:ubuntu-24_04-lts:server:latest" \
    --admin-username "$ADMIN_USERNAME" \
    --ssh-key-value "$SSH_KEY_PATH" \
    --nics "$NIC_NAME" \
    --authentication-type ssh \
    --os-disk-size-gb 128 \
    --os-disk-delete-option Delete \
    --storage-sku "Premium_LRS" \
    --custom-data "@${CLOUD_INIT_FILE}" \
    -o json

# ── Attach data disk ───────────────────────────────────────────────────
echo "=== Attaching ${DATA_DISK_SIZE_GB}GB data disk ==="
az vm disk attach \
    --resource-group "$RESOURCE_GROUP" \
    --vm-name "$VM_NAME" \
    --name "${VM_NAME}-data-disk" \
    --new \
    --size-gb "$DATA_DISK_SIZE_GB" \
    --sku "$DATA_DISK_SKU" \
    --lun 0 \
    -o none

# ── Output ──────────────────────────────────────────────────────────────
PUBLIC_IP=$(az network public-ip show \
    --resource-group "$RESOURCE_GROUP" \
    --name "$IP_NAME" \
    --query ipAddress -o tsv)

cat <<SUMMARY

==================================================================
  VM provisioned: ${VM_NAME}
  Size:           ${VM_SIZE} (~\$3.80/hr regular priority)
  vCPU/RAM:       48 / 384 GB
  Public IP:      ${PUBLIC_IP}
  SSH:            ssh ${ADMIN_USERNAME}@${PUBLIC_IP}

  Cloud-init is running (~10-15 min). Monitor with:
    ssh ${ADMIN_USERNAME}@${PUBLIC_IP} 'tail -f /var/log/cloud-init-output.log'

  Check completion:
    ssh ${ADMIN_USERNAME}@${PUBLIC_IP} 'test -f ~/workspace/.cloud-init-done && echo READY || echo STILL RUNNING'

  Verify install:
    ssh ${ADMIN_USERNAME}@${PUBLIC_IP} 'cat ~/workspace/install.log | tail -20'

  Run a batch job:
    ssh ${ADMIN_USERNAME}@${PUBLIC_IP}
    tmux new -s batch
    cd ~/workspace/sd-data-ingest/scripts/batch_processing
    source .env && set -a && . .env && set +a
    python process_from_raw.py --start-date 2023-06-22 --end-date 2023-07-11 \\
      --calibration-file ~/workspace/calibration/calibration_values.xlsx \\
      --output-container sd-tpos2023-20day-v05 \\
      --gps-container gpsdata --keep-raw --raw-cache-dir /mnt/data/raw_cache \\
      --n-workers 8 --memory-limit 24GB --upload-after \\
      --save-mvbs-netcdf --save-nasc-netcdf

  Deallocate (stop billing):
    az vm deallocate -g ${RESOURCE_GROUP} -n ${VM_NAME}

  Delete:
    az vm delete -g ${RESOURCE_GROUP} -n ${VM_NAME} --yes
    az disk delete -g ${RESOURCE_GROUP} -n ${VM_NAME}-data-disk --yes --no-wait
    az network nic delete -g ${RESOURCE_GROUP} -n ${NIC_NAME} --no-wait
    az network public-ip delete -g ${RESOURCE_GROUP} -n ${IP_NAME} --no-wait
==================================================================
SUMMARY
