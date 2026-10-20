#!/bin/bash
# Run from your Mac to push code + secrets to EC2.
# Usage: bash deploy/sync-to-ec2.sh <EC2_IP> [path/to/key.pem]
#
# Example:
#   bash deploy/sync-to-ec2.sh 54.123.45.67 ~/Downloads/migration-key.pem

set -e

EC2_IP="${1:?Usage: $0 <EC2_IP> [key.pem]}"
KEY="${2:-~/.ssh/id_rsa}"
REMOTE="ubuntu@$EC2_IP"
APP_DIR="$( cd "$(dirname "$0")/.." && pwd )"

SSH="ssh -i $KEY -o StrictHostKeyChecking=no"
SCP="scp -i $KEY -o StrictHostKeyChecking=no"

echo "=== Syncing app to $REMOTE ==="

# Create remote dir
$SSH $REMOTE "mkdir -p ~/migration-dashboard/data ~/migration-dashboard/secrets"

# Sync code (exclude venv, cache, local db)
rsync -az --exclude='.venv' --exclude='__pycache__' --exclude='*.pyc' \
  --exclude='data/' --exclude='.DS_Store' \
  -e "ssh -i $KEY -o StrictHostKeyChecking=no" \
  "$APP_DIR/" "$REMOTE:~/migration-dashboard/"

# Sync secrets (google token, certs)
if [ -d "$APP_DIR/secrets" ]; then
  rsync -az -e "ssh -i $KEY -o StrictHostKeyChecking=no" \
    "$APP_DIR/secrets/" "$REMOTE:~/migration-dashboard/secrets/"
fi

# Sync existing DB (first deploy only — comment out after initial seed)
if [ -f "$APP_DIR/data/migration.db" ]; then
  echo "Copying existing database..."
  $SCP "$APP_DIR/data/migration.db" "$REMOTE:~/migration-dashboard/data/migration.db"
fi

echo "=== Running setup on EC2 ==="
$SSH $REMOTE "bash ~/migration-dashboard/deploy/ec2-setup.sh"

echo ""
echo "=== Deployed! ==="
echo "SSH:  ssh -i $KEY $REMOTE"
echo "Logs: ssh -i $KEY $REMOTE 'tail -f ~/migration-dashboard/data/server.log'"
