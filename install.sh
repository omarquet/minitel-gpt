#!/bin/bash
# install.sh — Installation complète de MINITEL GPT sur Raspberry Pi
# Usage : sudo bash install.sh
set -e

PROJ_DIR="/home/minitel/minitel-gpt"
SERVICE_DIR="/etc/systemd/system"

echo "=== MINITEL GPT — Installation ==="

# ── Dépendances système ─────────────────────────────────────────────────────
echo "[1/6] Paquets système..."
apt-get update -q
apt-get install -y \
  python3-serial python3-requests python3-flask python3-dotenv \
  dnsmasq-base iw minicom

# ── Dépendances Python ──────────────────────────────────────────────────────
echo "[2/6] Paquets Python..."
python3 -m pip install pyfiglet --break-system-packages

# ── Désactiver le dnsmasq système (conflit port 53 avec le hotspot) ─────────
echo "[3/6] Nettoyage dnsmasq système..."
systemctl disable --now dnsmasq 2>/dev/null || true

# ── Permissions port série + répertoires ────────────────────────────────────
echo "[4/6] Permissions et répertoires..."
usermod -a -G dialout minitel
mkdir -p "$PROJ_DIR/logs" "$PROJ_DIR/config/knowledge"
chown -R minitel:minitel "$PROJ_DIR"

# ── Règle sudo (l'admin web redémarre le terminal sans mot de passe) ────────
echo "[5/6] Règle sudo..."
cp "$PROJ_DIR/config/minitel-gpt-sudoers" /etc/sudoers.d/minitel-gpt
chmod 440 /etc/sudoers.d/minitel-gpt
visudo -c -f /etc/sudoers.d/minitel-gpt

# ── Services systemd ────────────────────────────────────────────────────────
echo "[6/6] Services systemd..."
cp "$PROJ_DIR/config/minitel-chatgpt.service" "$SERVICE_DIR/"
cp "$PROJ_DIR/config/wifi-manager.service"    "$SERVICE_DIR/"
cp "$PROJ_DIR/config/admin-ui.service"        "$SERVICE_DIR/"
systemctl daemon-reload
systemctl enable minitel-chatgpt.service wifi-manager.service admin-ui.service

echo ""
echo "=== Installation terminée ==="
echo ""
echo "1. Créer le fichier .env avec la clé Mistral :"
echo "     cp $PROJ_DIR/config/env.example $PROJ_DIR/.env"
echo "     nano $PROJ_DIR/.env      # renseigner MISTRAL_KEY"
echo ""
echo "2. Démarrer les services :"
echo "     sudo systemctl start minitel-chatgpt wifi-manager admin-ui"
echo ""
echo "3. Admin web : http://<ip-du-pi>:8080   (mot de passe : mistral)"
echo "   (l'IP s'affiche aussi sur le Minitel via la touche GUIDE)"
echo ""
echo "Minitel : reçoit/affiche par défaut sur la plupart des modèles."
echo "          Si rien ne s'affiche : verifier les contacts + jumper FTDI sur 5V,"
echo "          et si besoin activer le mode peri-info (Fnct+T puis A)."
