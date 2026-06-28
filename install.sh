#!/usr/bin/env bash
# Installs mesh-ots-bridge on Raspberry Pi OS Bookworm.
# Run as root: sudo bash install.sh

set -e

INSTALL_DIR="/opt/mesh-ots-bridge"
SERVICE="mesh-ots-bridge"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

echo "==> Installing system dependencies..."
apt-get update -qq
apt-get install -y python3-tk python3-pip python3-venv

echo "==> Creating install directory..."
mkdir -p "$INSTALL_DIR"

echo "==> Copying files..."
cp "$SCRIPT_DIR"/*.py  "$INSTALL_DIR/"
cp "$SCRIPT_DIR/config.json" "$INSTALL_DIR/" 2>/dev/null || true

# Keep existing config if already present
if [ ! -f "$INSTALL_DIR/config.json" ]; then
    cp "$SCRIPT_DIR/config.json" "$INSTALL_DIR/"
fi

echo "==> Installing Python dependencies..."
pip3 install --break-system-packages meshtastic flask pyserial

echo "==> Setting permissions..."
chown -R ss:ss "$INSTALL_DIR"
chmod +x "$INSTALL_DIR/daemon.py" "$INSTALL_DIR/gui.py"

echo "==> Adding ss to dialout group (serial port access)..."
usermod -aG dialout ss

echo "==> Installing systemd service..."
cp "$SCRIPT_DIR/$SERVICE.service" /etc/systemd/system/
systemctl daemon-reload
systemctl enable "$SERVICE"
systemctl restart "$SERVICE"

echo "==> Installing GUI autostart..."
cp "$SCRIPT_DIR/$SERVICE-gui.desktop" /etc/xdg/autostart/

# Allow ss to start/stop/restart only this service without a password
SUDOERS_LINE="ss ALL=(ALL) NOPASSWD: /usr/bin/systemctl start $SERVICE, /usr/bin/systemctl stop $SERVICE, /usr/bin/systemctl restart $SERVICE"
SUDOERS_FILE="/etc/sudoers.d/mesh-ots-bridge"
echo "$SUDOERS_LINE" > "$SUDOERS_FILE"
chmod 0440 "$SUDOERS_FILE"

echo ""
echo "==> Done! Service status:"
systemctl status "$SERVICE" --no-pager || true
echo ""
echo "The GUI will auto-launch on next desktop login."
echo "To check logs: journalctl -u $SERVICE -f"
