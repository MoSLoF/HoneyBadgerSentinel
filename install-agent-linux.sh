#!/bin/bash
# ═══════════════════════════════════════════════════════════════════════
# HoneyBadger Sentinel - Linux Agent Installation Script
# Install on: iHBV-NAS-TT, OrangePi 6P, etc.
# Version: 1.1.0
# ═══════════════════════════════════════════════════════════════════════

set -e

echo "╔═══════════════════════════════════════════════════════════╗"
echo "║  🦡 HoneyBadger Sentinel Agent v1.1.0 - Installation     ║"
echo "╚═══════════════════════════════════════════════════════════╝"
echo ""

# Check if running as root
if [ "$EUID" -ne 0 ]; then
    echo "[!] Please run as root (sudo)"
    exit 1
fi

# Install Python dependencies
echo "[*] Installing Python dependencies..."
pip3 install --break-system-packages requests psutil 2>/dev/null || \
pip3 install requests psutil

# Copy agent script
echo "[*] Installing agent..."
mkdir -p /opt/hbv-sentinel
cp sentinel-agent-linux.py /opt/hbv-sentinel/
chmod +x /opt/hbv-sentinel/sentinel-agent-linux.py

# Create log directory
mkdir -p /var/log/hbv-sentinel

# Install as service (creates /etc/hbv-sentinel/agent.env)
echo "[*] Installing as systemd service..."
python3 /opt/hbv-sentinel/sentinel-agent-linux.py --install

echo ""
echo "╔═══════════════════════════════════════════════════════════╗"
echo "║  Installation Complete                                    ║"
echo "╚═══════════════════════════════════════════════════════════╝"
echo ""
echo "[✓] Agent installed"
echo ""
echo "Starting agent..."
systemctl start hbv-sentinel.service

sleep 2

echo ""
echo "Service Status:"
systemctl status hbv-sentinel.service --no-pager | head -15
echo ""
echo "Configuration: /etc/hbv-sentinel/agent.env"
echo ""
echo "Useful Commands:"
echo "  systemctl status hbv-sentinel"
echo "  systemctl restart hbv-sentinel"
echo "  journalctl -u hbv-sentinel -f"
echo ""
echo "To configure API key authentication:"
echo "  1. Edit /etc/hbv-sentinel/agent.env"
echo "  2. Set HBV_API_KEY=your-api-key"
echo "  3. systemctl restart hbv-sentinel"
echo ""
