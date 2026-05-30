#!/bin/bash
# DoSync PKI Rotation — annual hub certificate renewal
#
# Usage:
#   bash rotate_pki.sh                        # rotate hub cert (IP auto-detected)
#   bash rotate_pki.sh 192.168.100.109        # rotate hub cert with explicit IP
#   bash rotate_pki.sh --check                # check status only, no rotation
#   bash rotate_pki.sh --force                # rotate even if not near expiry
#
# What this script does:
#   1. Checks current PKI status and days remaining on each cert
#   2. Backs up current hub.crt and hub.key
#   3. Renews hub.crt and hub.key (the CA does NOT change)
#   4. Verifies the new cert chains correctly to the CA
#   5. Restarts the hub via systemd
#   6. Confirms the hub came back up
#   7. Prints manual steps for any connected client machines
#
# What this script does NOT do:
#   - Rotate the CA (valid 10 years — does not require annual rotation)
#   - Modify adapter certs (rotate with: python3 -m dosync.security renew <name>)
#   - Automatically distribute the CA cert to clients (manual step, not needed
#     for hub cert rotation since the CA is unchanged)
#
# The CA is not rotated here because doing so would invalidate every client
# that already trusts it. The CA is the system's root of trust — its rotation
# is a separate, deliberate event. See DESIGN-PRINCIPLES.md for details.

set -e

REPO_DIR="$HOME/dosync-protocol"
CERTS_DIR="$REPO_DIR/certs"
BACKUP_DIR="$REPO_DIR/certs/backup"
HUB_IP="${1:-}"
CHECK_ONLY=false
FORCE=false

# ── Parse arguments ───────────────────────────────────────────────────────────

for arg in "$@"; do
    case $arg in
        --check) CHECK_ONLY=true ;;
        --force) FORCE=true ;;
    esac
done

# If the first argument looks like an IP, use it
if [[ "$1" =~ ^[0-9]+\.[0-9]+\.[0-9]+\.[0-9]+$ ]]; then
    HUB_IP="$1"
fi

# ── Colors ────────────────────────────────────────────────────────────────────

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

ok()   { echo -e "  ${GREEN}✓${NC}  $1"; }
warn() { echo -e "  ${YELLOW}⚠${NC}  $1"; }
err()  { echo -e "  ${RED}✗${NC}  $1"; }
info() { echo -e "  ${BLUE}→${NC}  $1"; }

# ── Pre-flight checks ─────────────────────────────────────────────────────────

echo ""
echo "==================================="
echo "  DoSync PKI Rotation"
echo "==================================="
echo ""

# Verify we are in the correct repo
if [ ! -d "$REPO_DIR" ]; then
    err "Repo not found at $REPO_DIR"
    exit 1
fi

cd "$REPO_DIR"

# Verify CA exists (should never be missing in a running deployment)
if [ ! -f "$CERTS_DIR/ca.crt" ] || [ ! -f "$CERTS_DIR/ca.key" ]; then
    err "CA not found at $CERTS_DIR — PKI is not initialized"
    info "Run: bash setup_pki.sh"
    exit 1
fi

# Activate virtualenv if present
if [ -f "venv/bin/activate" ]; then
    source venv/bin/activate
fi

# Auto-detect hub IP if not provided
if [ -z "$HUB_IP" ]; then
    HUB_IP=$(hostname -I | awk '{print $1}')
fi

# ── Check current status ──────────────────────────────────────────────────────

echo "Current PKI status:"
echo ""

CA_EXPIRY=$(openssl x509 -in "$CERTS_DIR/ca.crt" -noout -enddate 2>/dev/null | cut -d= -f2)
HUB_EXPIRY=$(openssl x509 -in "$CERTS_DIR/hub.crt" -noout -enddate 2>/dev/null | cut -d= -f2)

# Days remaining on hub cert
HUB_DAYS=$(python3 -c "
import datetime, subprocess
r = subprocess.run(['openssl','x509','-in','$CERTS_DIR/hub.crt','-noout','-enddate'],
    capture_output=True, text=True)
date_str = r.stdout.strip().split('=',1)[1]
exp = datetime.datetime.strptime(date_str, '%b %d %H:%M:%S %Y %Z').replace(
    tzinfo=datetime.timezone.utc)
print((exp - datetime.datetime.now(datetime.timezone.utc)).days)
" 2>/dev/null || echo "0")

ok "CA cert:      $CA_EXPIRY"

if [ "$HUB_DAYS" -le 0 ]; then
    err "Hub cert:     $HUB_EXPIRY  — EXPIRED"
elif [ "$HUB_DAYS" -le 30 ]; then
    warn "Hub cert:     $HUB_EXPIRY  — expires in ${HUB_DAYS} days"
else
    ok "Hub cert:     $HUB_EXPIRY  — ${HUB_DAYS} days remaining"
fi

# Check adapter certs
if [ -d "$CERTS_DIR/adapters" ]; then
    for cert in "$CERTS_DIR/adapters"/*.crt; do
        [ -f "$cert" ] || continue
        name=$(basename "$cert" .crt)
        days=$(python3 -c "
import datetime, subprocess
r = subprocess.run(['openssl','x509','-in','$cert','-noout','-enddate'],
    capture_output=True, text=True)
date_str = r.stdout.strip().split('=',1)[1]
exp = datetime.datetime.strptime(date_str, '%b %d %H:%M:%S %Y %Z').replace(
    tzinfo=datetime.timezone.utc)
print((exp - datetime.datetime.now(datetime.timezone.utc)).days)
" 2>/dev/null || echo "0")
        if [ "$days" -le 0 ]; then
            err "Adapter $name: EXPIRED"
        elif [ "$days" -le 30 ]; then
            warn "Adapter $name: expires in ${days} days"
        else
            ok "Adapter $name: ${days} days remaining"
        fi
    done
fi

echo ""

# Check-only mode: exit here
if [ "$CHECK_ONLY" = true ]; then
    echo "--check mode: no changes made."
    echo ""
    exit 0
fi

# ── Decide whether to rotate ──────────────────────────────────────────────────

if [ "$HUB_DAYS" -gt 30 ] && [ "$FORCE" = false ]; then
    warn "Hub cert expires in ${HUB_DAYS} days — rotation not required yet."
    info "Use --force to rotate anyway."
    echo ""
    exit 0
fi

# ── Backup ────────────────────────────────────────────────────────────────────

TIMESTAMP=$(date +%Y%m%d_%H%M%S)
BACKUP_PATH="$BACKUP_DIR/$TIMESTAMP"

echo "Backing up current certs..."
mkdir -p "$BACKUP_PATH"
cp "$CERTS_DIR/hub.crt" "$BACKUP_PATH/hub.crt"
cp "$CERTS_DIR/hub.key" "$BACKUP_PATH/hub.key"
ok "Backup saved to: $BACKUP_PATH"
echo ""

# ── Rotate hub cert ───────────────────────────────────────────────────────────

echo "Renewing hub certificate..."
echo ""

PYTHONPATH=. python3 -m dosync.security renew hub --ip "$HUB_IP" 2>&1 | \
    while IFS= read -r line; do echo "  $line"; done

echo ""

# Verify the new cert chains correctly to the CA
if openssl verify -CAfile "$CERTS_DIR/ca.crt" "$CERTS_DIR/hub.crt" &>/dev/null; then
    NEW_EXPIRY=$(openssl x509 -in "$CERTS_DIR/hub.crt" -noout -enddate | cut -d= -f2)
    ok "New hub cert valid — expires: $NEW_EXPIRY"
else
    err "New hub cert failed chain verification"
    info "Restoring backup..."
    cp "$BACKUP_PATH/hub.crt" "$CERTS_DIR/hub.crt"
    cp "$BACKUP_PATH/hub.key" "$CERTS_DIR/hub.key"
    err "Backup restored. No changes applied."
    exit 1
fi

echo ""

# ── Restart hub ───────────────────────────────────────────────────────────────

echo "Restarting DoSync hub..."

if systemctl is-active --quiet dosync; then
    sudo systemctl restart dosync
    sleep 3

    if systemctl is-active --quiet dosync; then
        ok "Hub restarted successfully"
    else
        err "Hub did not come back up after restart"
        info "Check logs: sudo journalctl -u dosync -n 50"
        info "To restore manually:"
        info "  cp $BACKUP_PATH/hub.crt $CERTS_DIR/hub.crt"
        info "  cp $BACKUP_PATH/hub.key $CERTS_DIR/hub.key"
        info "  sudo systemctl restart dosync"
        exit 1
    fi
else
    warn "dosync systemd service is not running"
    info "Restart the hub manually to apply the new certificate"
fi

echo ""

# ── Post-rotation instructions ────────────────────────────────────────────────

echo "==================================="
echo "  Rotation complete"
echo ""
echo "  The CA cert did NOT change — no redistribution needed."
echo ""
echo "  Manual steps on any connected client machine:"
echo ""
echo "  1. Verify from the client:"
echo "     DOSYNC_TOKEN=<token> \\"
echo "     DOSYNC_CA_CERT=<path-to-ca.crt> \\"
echo "     python3 certify.py --host $HUB_IP --port 47200 --tier standard"
echo ""
echo "  2. If the CA cert is not yet on the client machine:"
echo "     scp rgiuliani@$HUB_IP:$CERTS_DIR/ca.crt <destination>"
echo ""
echo "  Previous backup: $BACKUP_PATH"
echo "==================================="
echo ""
