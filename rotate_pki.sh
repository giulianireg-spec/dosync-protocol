#!/bin/bash
# DoSync PKI Rotation — renovación anual del certificado del hub
#
# Uso:
#   bash rotate_pki.sh                        # rota hub cert (IP auto-detectada)
#   bash rotate_pki.sh 192.168.100.109        # rota hub cert con IP explícita
#   bash rotate_pki.sh --check                # solo verifica estado, no rota
#   bash rotate_pki.sh --force                # rota aunque no esté próximo a vencer
#
# Qué hace este script:
#   1. Verifica el estado actual de la PKI
#   2. Hace backup de los certs actuales
#   3. Renueva hub.crt y hub.key (la CA NO cambia)
#   4. Reinicia el hub via systemd
#   5. Verifica que el hub levantó correctamente
#   6. Imprime instrucciones para actualizar el Mac
#
# Qué NO hace:
#   - Rotar la CA (válida 10 años — no requiere rotación anual)
#   - Modificar certs de adapters (rotar con: python3 -m dosync.security renew <nombre>)
#   - Distribuir automáticamente el CA cert al Mac (requiere acción manual)
#
# La CA no se rota en este script porque hacerlo invalidaría todos los clientes
# que ya confían en ella (Mac, certify.py, Claude Desktop). La CA es la raíz de
# confianza del sistema — su rotación es un proceso separado y deliberado.

set -e

REPO_DIR="$HOME/dosync-protocol"
CERTS_DIR="$REPO_DIR/certs"
BACKUP_DIR="$REPO_DIR/certs/backup"
HUB_IP="${1:-}"
CHECK_ONLY=false
FORCE=false

# ── Parsear argumentos ────────────────────────────────────────────────────────

for arg in "$@"; do
    case $arg in
        --check) CHECK_ONLY=true ;;
        --force) FORCE=true ;;
    esac
done

# Si el primer argumento parece una IP, úsarla
if [[ "$1" =~ ^[0-9]+\.[0-9]+\.[0-9]+\.[0-9]+$ ]]; then
    HUB_IP="$1"
fi

# ── Colores ───────────────────────────────────────────────────────────────────

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

ok()   { echo -e "  ${GREEN}✓${NC}  $1"; }
warn() { echo -e "  ${YELLOW}⚠${NC}  $1"; }
err()  { echo -e "  ${RED}✗${NC}  $1"; }
info() { echo -e "  ${BLUE}→${NC}  $1"; }

# ── Verificaciones previas ────────────────────────────────────────────────────

echo ""
echo "==================================="
echo "  DoSync PKI Rotation"
echo "==================================="
echo ""

# Verificar que estamos en el repo correcto
if [ ! -d "$REPO_DIR" ]; then
    err "Repo no encontrado en $REPO_DIR"
    exit 1
fi

cd "$REPO_DIR"

# Verificar que existe la CA (no debería faltar nunca)
if [ ! -f "$CERTS_DIR/ca.crt" ] || [ ! -f "$CERTS_DIR/ca.key" ]; then
    err "CA no encontrada en $CERTS_DIR — la PKI no está inicializada"
    info "Correr: bash setup_pki.sh"
    exit 1
fi

# Activar virtualenv
if [ -f "venv/bin/activate" ]; then
    source venv/bin/activate
fi

# Detectar IP del hub si no se pasó
if [ -z "$HUB_IP" ]; then
    HUB_IP=$(hostname -I | awk '{print $1}')
fi

# ── Verificar estado actual ───────────────────────────────────────────────────

echo "Estado actual de la PKI:"
echo ""

# Fechas de los certs actuales
CA_EXPIRY=$(openssl x509 -in "$CERTS_DIR/ca.crt" -noout -enddate 2>/dev/null | cut -d= -f2)
HUB_EXPIRY=$(openssl x509 -in "$CERTS_DIR/hub.crt" -noout -enddate 2>/dev/null | cut -d= -f2)

# Días restantes del hub cert
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
    err "Hub cert:     $HUB_EXPIRY  — EXPIRADO"
elif [ "$HUB_DAYS" -le 30 ]; then
    warn "Hub cert:     $HUB_EXPIRY  — vence en ${HUB_DAYS} días"
else
    ok "Hub cert:     $HUB_EXPIRY  — ${HUB_DAYS} días restantes"
fi

# Verificar certs de adapters
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
            err "Adapter $name: EXPIRADO"
        elif [ "$days" -le 30 ]; then
            warn "Adapter $name: vence en ${days} días"
        else
            ok "Adapter $name: ${days} días restantes"
        fi
    done
fi

echo ""

# Si solo se pidió verificar, terminar acá
if [ "$CHECK_ONLY" = true ]; then
    echo "Modo --check: no se realizaron cambios."
    echo ""
    exit 0
fi

# ── Decidir si rotar ──────────────────────────────────────────────────────────

if [ "$HUB_DAYS" -gt 30 ] && [ "$FORCE" = false ]; then
    warn "El hub cert vence en ${HUB_DAYS} días — no es necesario rotar todavía."
    info "Usar --force para rotar de todas formas."
    echo ""
    exit 0
fi

# ── Backup ────────────────────────────────────────────────────────────────────

TIMESTAMP=$(date +%Y%m%d_%H%M%S)
BACKUP_PATH="$BACKUP_DIR/$TIMESTAMP"

echo "Haciendo backup de certs actuales..."
mkdir -p "$BACKUP_PATH"
cp "$CERTS_DIR/hub.crt" "$BACKUP_PATH/hub.crt"
cp "$CERTS_DIR/hub.key" "$BACKUP_PATH/hub.key"
ok "Backup guardado en: $BACKUP_PATH"
echo ""

# ── Rotación del hub cert ─────────────────────────────────────────────────────

echo "Renovando hub certificate..."
echo ""

PYTHONPATH=. python3 -m dosync.security renew hub --ip "$HUB_IP" 2>&1 | \
    while IFS= read -r line; do echo "  $line"; done

echo ""

# Verificar que el nuevo cert es válido
if openssl verify -CAfile "$CERTS_DIR/ca.crt" "$CERTS_DIR/hub.crt" &>/dev/null; then
    NEW_EXPIRY=$(openssl x509 -in "$CERTS_DIR/hub.crt" -noout -enddate | cut -d= -f2)
    ok "Nuevo hub cert válido — vence: $NEW_EXPIRY"
else
    err "El nuevo hub cert no pasa la verificación de cadena"
    info "Restaurando backup..."
    cp "$BACKUP_PATH/hub.crt" "$CERTS_DIR/hub.crt"
    cp "$BACKUP_PATH/hub.key" "$CERTS_DIR/hub.key"
    err "Backup restaurado. Sin cambios aplicados."
    exit 1
fi

echo ""

# ── Reinicio del hub ──────────────────────────────────────────────────────────

echo "Reiniciando hub DoSync..."

if systemctl is-active --quiet dosync; then
    sudo systemctl restart dosync
    sleep 3

    if systemctl is-active --quiet dosync; then
        ok "Hub reiniciado correctamente"
    else
        err "El hub no levantó después del reinicio"
        info "Ver logs: sudo journalctl -u dosync -n 50"
        info "Restaurando backup manualmente si es necesario:"
        info "  cp $BACKUP_PATH/hub.crt $CERTS_DIR/hub.crt"
        info "  cp $BACKUP_PATH/hub.key $CERTS_DIR/hub.key"
        info "  sudo systemctl restart dosync"
        exit 1
    fi
else
    warn "Servicio dosync no está corriendo via systemd"
    info "Reiniciar manualmente el hub para aplicar los nuevos certs"
fi

echo ""

# ── Instrucciones post-rotación ───────────────────────────────────────────────

echo "==================================="
echo "  Rotación completada"
echo ""
echo "  El CA cert NO cambió — no es necesario redistribuirlo."
echo ""
echo "  Pasos manuales en el Mac:"
echo ""
echo "  1. Actualizar copia local del CA cert (opcional, no cambió):"
echo "     scp rgiuliani@$HUB_IP:$CERTS_DIR/ca.crt ~/Desktop/dosync-ca.crt"
echo ""
echo "  2. Verificar desde el Mac:"
echo "     DOSYNC_TOKEN=<token> \\"
echo "     DOSYNC_CA_CERT=~/Desktop/dosync-ca.crt \\"
echo "     python3 certify.py --host $HUB_IP --port 47200 --tier standard"
echo ""
echo "  3. Actualizar Claude Desktop si era necesario:"
echo "     cat ~/Library/Application\\ Support/Claude/claude_desktop_config.yaml"
echo "     # Verificar que DOSYNC_HUB_URL apunta a https://$HUB_IP:47200"
echo ""
echo "  Backup anterior en: $BACKUP_PATH"
echo "==================================="
echo ""
