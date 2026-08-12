#!/bin/bash
# DoSync PKI Setup — corre una sola vez en la Pi
# Uso: bash setup_pki.sh
# Con IP explícita: bash setup_pki.sh <hub-address>

set -e

HUB_IP="${1:-}"
REPO_DIR="$HOME/dosync-protocol"

echo "==================================="
echo "  DoSync PKI Setup"
echo "==================================="

# Detectar IP si no se pasó como argumento
if [ -z "$HUB_IP" ]; then
    HUB_IP=$(hostname -I | awk '{print $1}')
    echo "  Hub IP detectada: $HUB_IP"
else
    echo "  Hub IP: $HUB_IP"
fi

echo ""

# Verificar que openssl está disponible
if ! command -v openssl &> /dev/null; then
    echo "ERROR: openssl no encontrado. Instalar con:"
    echo "  sudo apt-get install openssl"
    exit 1
fi
echo "  openssl: $(openssl version)"

# Verificar que el repo existe
if [ ! -d "$REPO_DIR" ]; then
    echo "ERROR: Repo no encontrado en $REPO_DIR"
    exit 1
fi

cd "$REPO_DIR"

# Verificar que security.py está en dosync/
if [ ! -f "dosync/security.py" ]; then
    echo "ERROR: dosync/security.py no encontrado."
    echo "Copiá el archivo antes de correr este script."
    exit 1
fi

# Activar virtualenv si existe
if [ -f "venv/bin/activate" ]; then
    source venv/bin/activate
    echo "  virtualenv: activado"
fi

echo ""
echo "Generando PKI..."
echo ""

# Correr setup con la IP detectada
PYTHONPATH=. python3 -m dosync.security setup --ip "$HUB_IP" --hostname "dosync-hub"

echo ""
echo "==================================="
echo "  Setup completo."
echo ""
echo "  Para iniciar el hub con HTTPS:"
echo "  uvicorn server:app \\"
echo "    --host 0.0.0.0 --port 47200 \\"
echo "    --ssl-keyfile certs/hub.key \\"
echo "    --ssl-certfile certs/hub.crt"
echo ""
echo "  Para mTLS (requiere cert de cliente):"
echo "  uvicorn server:app \\"
echo "    --host 0.0.0.0 --port 47200 \\"
echo "    --ssl-keyfile certs/hub.key \\"
echo "    --ssl-certfile certs/hub.crt \\"
echo "    --ssl-ca-certs certs/ca.crt"
echo "==================================="
