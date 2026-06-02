#!/bin/bash
# install.sh — Setup complet MinitelGPT sur Raspberry Pi Zero 2 W
# Usage : sudo bash install.sh

set -e
PROJ_DIR="/home/minitel/minitel-gpt"
SERVICE_DIR="/etc/systemd/system"

echo "=== MinitelGPT Install ==="

# ── Dépendances système ────────────────────────────────────────────────────
echo "[1/5] Dépendances système..."
apt-get update -q
apt-get install -y python3-serial python3-requests python3-flask \
  python3-dotenv dnsmasq minicom

# ── Dépendances Python ────────────────────────────────────────────────────
echo "[2/5] Dépendances Python..."
python3 -m pip install anthropic --break-system-packages

# ── Permissions UART ──────────────────────────────────────────────────────
echo "[3/5] Permissions UART..."
usermod -a -G dialout minitel

# ── Répertoires et logs ───────────────────────────────────────────────────
echo "[4/5] Répertoires..."
mkdir -p "$PROJ_DIR/logs"
chown -R minitel:minitel "$PROJ_DIR"

# ── Services systemd ──────────────────────────────────────────────────────
echo "[5/5] Services systemd..."
cp "$PROJ_DIR/config/wifi-manager.service" "$SERVICE_DIR/"
cp "$PROJ_DIR/config/boot-notify.service" "$SERVICE_DIR/"
cp "$PROJ_DIR/config/minitel-chatgpt.service" "$SERVICE_DIR/"

systemctl daemon-reload
systemctl enable wifi-manager.service
systemctl enable boot-notify.service
systemctl enable minitel-chatgpt.service

echo ""
echo "=== Installation terminée ==="
echo ""
echo "IMPORTANT : créer le fichier .env avant de démarrer :"
echo "  cp $PROJ_DIR/config/env.example $PROJ_DIR/.env"
echo "  nano $PROJ_DIR/.env"
echo ""
echo "Puis démarrer les services :"
echo "  systemctl start boot-notify"
echo "  systemctl start minitel-chatgpt"
echo ""
echo "Test UART loopback (pont pin8↔pin10 requis) :"
echo "  python3 $PROJ_DIR/services/test_uart.py --loopback"
