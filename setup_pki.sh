#!/bin/bash
# DoSync PKI setup — run once on the hub machine
# Uso: bash setup_pki.sh
# With an explicit address: bash setup_pki.sh <hub-address>

set -e

HUB_IP="${1:-}"
REPO_DIR="$HOME/dosync-protocol"

echo "==================================="
echo "  DoSync PKI Setup"
echo "==================================="

# Detect the address when none was given
if [ -z "$HUB_IP" ]; then
    HUB_IP=$(hostname -I | awk '{print $1}')
    echo "  Hub IP detected: $HUB_IP"
else
    echo "  Hub IP: $HUB_IP"
fi

echo ""

# Check that openssl is available
if ! command -v openssl &> /dev/null; then
    echo "ERROR: openssl not found. Install it with:"
    echo "  sudo apt-get install openssl"
    exit 1
fi
echo "  openssl: $(openssl version)"

# Check that the repository is there
if [ ! -d "$REPO_DIR" ]; then
    echo "ERROR: repository not found at $REPO_DIR"
    exit 1
fi

cd "$REPO_DIR"

# Check that security.py is in dosync/
if [ ! -f "dosync/security.py" ]; then
    echo "ERROR: dosync/security.py not found."
    echo "Copy the file before running this script."
    exit 1
fi

# Activar virtualenv si existe
if [ -f "venv/bin/activate" ]; then
    source venv/bin/activate
    echo "  virtualenv: active"
fi

echo ""
echo "Generating PKI..."
echo ""

# Run setup with the detected address
PYTHONPATH=. python3 -m dosync.security setup --ip "$HUB_IP" --hostname "dosync-hub"

echo ""
echo "==================================="
echo "  Setup complete."
echo ""
echo "  To start the hub with HTTPS:"
echo "  uvicorn server:app \\"
echo "    --host 0.0.0.0 --port 47200 \\"
echo "    --ssl-keyfile certs/hub.key \\"
echo "    --ssl-certfile certs/hub.crt"
echo ""
echo "  For mTLS (requires a client certificate):"
echo "  uvicorn server:app \\"
echo "    --host 0.0.0.0 --port 47200 \\"
echo "    --ssl-keyfile certs/hub.key \\"
echo "    --ssl-certfile certs/hub.crt \\"
echo "    --ssl-ca-certs certs/ca.crt"
echo "==================================="
