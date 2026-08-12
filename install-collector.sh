#!/bin/bash
# ═══════════════════════════════════════════════════════════════════════
# HoneyBadger Sentinel - Collector Installation Script
# Install on: Collector server
# Version: 1.1.4
#
# CREDENTIAL RECONCILIATION (review finding H-01):
#   Two files hold the collector's API key on disk:
#     /etc/hbv-sentinel/collector.env   (HBV_API_KEY=...)
#     /etc/hbv-sentinel/api.key         (0600, one-line token)
#   collector.env is authoritative — the running collector reads it via
#   systemd's EnvironmentFile. This installer delegates all creation and
#   reconciliation of those two files to scripts/reconcile-credentials.sh,
#   which handles fresh install / upgrade / reinstall / partial / mismatch
#   as separate branches, refuses to guess on mismatch, and is directly
#   unit-tested (tests/test_installer_migration.py).
# ═══════════════════════════════════════════════════════════════════════

set -euo pipefail

echo "╔═══════════════════════════════════════════════════════════╗"
echo "║  🦡 HoneyBadger Sentinel Collector v1.1.4 - Installation ║"
echo "╚═══════════════════════════════════════════════════════════╝"
echo ""

if [ "$EUID" -ne 0 ]; then
    echo "[!] Please run as root (sudo)"
    exit 1
fi

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"

echo "[*] Creating directories..."
mkdir -p /opt/hbv-sentinel
mkdir -p /var/log/hbv-sentinel
mkdir -p /etc/hbv-sentinel
chmod 750 /etc/hbv-sentinel

echo "[*] Installing Python dependencies..."
if [ -f "$SCRIPT_DIR/requirements.txt" ]; then
    pip3 install --break-system-packages -r "$SCRIPT_DIR/requirements.txt" 2>/dev/null || \
    pip3 install -r "$SCRIPT_DIR/requirements.txt"
else
    pip3 install --break-system-packages fastapi uvicorn 'pydantic>=2.5' requests psutil 2>/dev/null || \
    pip3 install fastapi uvicorn 'pydantic>=2.5' requests psutil
fi

echo "[*] Installing collector..."
cp "$SCRIPT_DIR/sentinel-collector.py" /opt/hbv-sentinel/
chmod +x /opt/hbv-sentinel/sentinel-collector.py

# Install (or refresh) the reconciler helper alongside the collector, so an
# operator running a later reinstall gets the reconciler that ships with
# whatever collector build they installed most recently.
mkdir -p /opt/hbv-sentinel/scripts
cp "$SCRIPT_DIR/scripts/reconcile-credentials.sh" /opt/hbv-sentinel/scripts/
cp "$SCRIPT_DIR/scripts/invoke-reconciler.sh"      /opt/hbv-sentinel/scripts/
cp "$SCRIPT_DIR/scripts/collector.env.template"    /opt/hbv-sentinel/scripts/
chmod +x /opt/hbv-sentinel/scripts/reconcile-credentials.sh
chmod +x /opt/hbv-sentinel/scripts/invoke-reconciler.sh

ENV_FILE="/etc/hbv-sentinel/collector.env"
KEY_FILE="/etc/hbv-sentinel/api.key"
TEMPLATE="/opt/hbv-sentinel/scripts/collector.env.template"
RECONCILER="/opt/hbv-sentinel/scripts/reconcile-credentials.sh"

echo "[*] Reconciling collector credentials..."
# The wrapper propagates the reconciler's original nonzero status correctly
# (review finding M-01). Do NOT re-introduce `if ! reconciler; then rc=$?`
# here — `$?` after negation is 0, not the underlying failure code.
ENV_FILE="$ENV_FILE" KEY_FILE="$KEY_FILE" TEMPLATE="$TEMPLATE" \
RECONCILER="$RECONCILER" \
    "$SCRIPT_DIR/scripts/invoke-reconciler.sh" || exit $?

echo "[*] Creating systemd service..."
cat > /etc/systemd/system/hbv-sentinel-collector.service << 'EOF'
[Unit]
Description=HoneyBadger Sentinel Collector
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=root
WorkingDirectory=/opt/hbv-sentinel
ExecStart=/usr/bin/python3 /opt/hbv-sentinel/sentinel-collector.py
Restart=always
RestartSec=10
EnvironmentFile=-/etc/hbv-sentinel/collector.env
TimeoutStopSec=30
KillMode=mixed
KillSignal=SIGTERM

[Install]
WantedBy=multi-user.target
EOF

systemctl daemon-reload
echo "[*] Enabling and (re)starting service..."
systemctl enable hbv-sentinel-collector.service
systemctl restart hbv-sentinel-collector.service

sleep 2

echo ""
echo "╔═══════════════════════════════════════════════════════════╗"
echo "║  Installation Complete                                    ║"
echo "╚═══════════════════════════════════════════════════════════╝"
echo ""
echo "[✓] Collector installed and running on 127.0.0.1:8443"
echo ""
echo "Service status:"
systemctl status hbv-sentinel-collector.service --no-pager | head -15
echo ""
echo "── Next steps ──────────────────────────────────────────────"
echo ""
echo "  1. Health check (loopback, no auth required):"
echo "     curl http://127.0.0.1:8443/health"
echo ""
echo "  2. Authenticated stats check:"
echo "     curl -H \"X-API-Key: \$(sudo cat $KEY_FILE)\" http://127.0.0.1:8443/api/stats"
echo ""
echo "     ($KEY_FILE now contains the ACTIVE key the running collector uses;"
echo "      reconciliation guarantees it matches HBV_API_KEY in $ENV_FILE.)"
echo ""
echo "  3. To reach the collector from other hosts:"
echo "     - Deploy a TLS-terminating reverse proxy (nginx, caddy) on this box"
echo "     - Point agents at https://<this-host>:<proxy-port>/api/beacon"
echo "     - Copy the API key from $KEY_FILE to every agent's HBV_API_KEY"
echo "     - Do NOT change HBV_HOST until the proxy is proven working"
echo ""
echo "  4. Configuration file: $ENV_FILE"
echo "     API key file:       $KEY_FILE  (mode 0600)"
echo ""
echo "  Management commands:"
echo "    systemctl status hbv-sentinel-collector"
echo "    systemctl restart hbv-sentinel-collector"
echo "    journalctl -u hbv-sentinel-collector -f"
echo ""
