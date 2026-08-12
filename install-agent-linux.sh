#!/bin/bash
# ═══════════════════════════════════════════════════════════════════════
# HoneyBadger Sentinel - Linux Agent Installation Script
# Install on: Linux agent hosts
# Version: 1.1.2
# ═══════════════════════════════════════════════════════════════════════

set -euo pipefail

echo "╔═══════════════════════════════════════════════════════════╗"
echo "║  🦡 HoneyBadger Sentinel Agent v1.1.2 - Installation     ║"
echo "╚═══════════════════════════════════════════════════════════╝"
echo ""

if [ "$EUID" -ne 0 ]; then
    echo "[!] Please run as root (sudo)"
    exit 1
fi

echo "[*] Installing Python dependencies..."
pip3 install --break-system-packages requests psutil 2>/dev/null || \
pip3 install requests psutil

echo "[*] Installing agent..."
mkdir -p /opt/hbv-sentinel
cp sentinel-agent-linux.py /opt/hbv-sentinel/
chmod +x /opt/hbv-sentinel/sentinel-agent-linux.py

mkdir -p /var/log/hbv-sentinel

echo "[*] Installing as systemd service..."
python3 /opt/hbv-sentinel/sentinel-agent-linux.py --install

echo ""
echo "╔═══════════════════════════════════════════════════════════╗"
echo "║  Installation Complete                                    ║"
echo "╚═══════════════════════════════════════════════════════════╝"
echo ""
echo "[✓] Agent installed"
echo ""
echo "── Required configuration before the agent will beacon ─────"
echo ""
echo "  Edit /etc/hbv-sentinel/agent.env and set at minimum:"
echo ""
echo "    HBV_COLLECTOR_URL=https://<collector-host>:8443/api/beacon"
echo "    HBV_API_KEY=<paste the collector's API key here>"
echo ""
echo "  If your collector uses a private-CA TLS certificate, also set:"
echo "    HBV_TLS_CA_BUNDLE=/etc/hbv-sentinel/ca.pem"
echo ""
echo "  Then start the agent:"
echo "    systemctl start hbv-sentinel"
echo ""
echo "  The agent REFUSES to beacon over cleartext http:// by default."
echo "  For a lab-only override, set HBV_ALLOW_INSECURE=true."
echo ""
echo "  Management:"
echo "    systemctl status hbv-sentinel"
echo "    systemctl restart hbv-sentinel"
echo "    journalctl -u hbv-sentinel -f"
echo ""
