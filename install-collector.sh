#!/bin/bash
# ═══════════════════════════════════════════════════════════════════════
# HoneyBadger Sentinel - Collector Installation Script
# Install on: Collector server
# Version: 1.1.0
# ═══════════════════════════════════════════════════════════════════════

set -e

echo "╔═══════════════════════════════════════════════════════════╗"
echo "║  🦡 HoneyBadger Sentinel Collector v1.1.0 - Installation ║"
echo "╚═══════════════════════════════════════════════════════════╝"
echo ""

# Check if running as root
if [ "$EUID" -ne 0 ]; then
    echo "[!] Please run as root (sudo)"
    exit 1
fi

# Create directories
echo "[*] Creating directories..."
mkdir -p /opt/hbv-sentinel
mkdir -p /var/log/hbv-sentinel
mkdir -p /etc/hbv-sentinel

# Install Python dependencies
echo "[*] Installing Python dependencies..."
if [ -f requirements.txt ]; then
    pip3 install --break-system-packages -r requirements.txt 2>/dev/null || \
    pip3 install -r requirements.txt
else
    pip3 install --break-system-packages fastapi uvicorn pydantic requests psutil 2>/dev/null || \
    pip3 install fastapi uvicorn pydantic requests psutil
fi

# Copy collector script
echo "[*] Installing collector..."
cp sentinel-collector.py /opt/hbv-sentinel/
chmod +x /opt/hbv-sentinel/sentinel-collector.py

# Create environment file if it doesn't exist
ENV_FILE="/etc/hbv-sentinel/collector.env"
if [ ! -f "$ENV_FILE" ]; then
    echo "[*] Creating configuration file..."
    cat > "$ENV_FILE" << 'ENVEOF'
# HoneyBadger Sentinel Collector Configuration
# Uncomment and modify as needed

# Server Settings
# HBV_HOST=0.0.0.0
# HBV_PORT=8443

# Database
# HBV_DB_PATH=/opt/hbv-sentinel/sentinel.db
# HBV_RETENTION_DAYS=30

# Authentication (enable for production)
# HBV_API_KEY_REQUIRED=false
# HBV_API_KEY=your-secure-api-key-here

# CORS (comma-separated list of allowed origins)
# HBV_ALLOWED_ORIGINS=*

# Rate Limiting
# HBV_RATE_LIMIT_REQUESTS=100
# HBV_RATE_LIMIT_WINDOW=60

# Alert Thresholds
# HBV_ALERT_CPU=90
# HBV_ALERT_MEMORY=90
# HBV_ALERT_DISK=90
# HBV_ALERT_GPU_TEMP=85

# Logging
# HBV_LOG_LEVEL=INFO
ENVEOF
    chmod 600 "$ENV_FILE"
fi

# Create systemd service
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
# Load environment from config file
EnvironmentFile=-/etc/hbv-sentinel/collector.env
# Graceful shutdown
TimeoutStopSec=30
KillMode=mixed
KillSignal=SIGTERM

[Install]
WantedBy=multi-user.target
EOF

# Reload systemd
systemctl daemon-reload

# Enable and start service
echo "[*] Enabling and starting service..."
systemctl enable hbv-sentinel-collector.service
systemctl start hbv-sentinel-collector.service

# Wait a moment for service to start
sleep 2

# Check status
echo ""
echo "╔═══════════════════════════════════════════════════════════╗"
echo "║  Installation Complete                                    ║"
echo "╚═══════════════════════════════════════════════════════════╝"
echo ""
echo "[✓] Collector installed and running"
echo ""
echo "Service Status:"
systemctl status hbv-sentinel-collector.service --no-pager | head -15
echo ""
echo "Dashboard: http://$(hostname -I | awk '{print $1}'):8443"
echo "API Docs:  http://$(hostname -I | awk '{print $1}'):8443/docs"
echo ""
echo "Configuration: $ENV_FILE"
echo ""
echo "Useful Commands:"
echo "  systemctl status hbv-sentinel-collector"
echo "  systemctl restart hbv-sentinel-collector"
echo "  journalctl -u hbv-sentinel-collector -f"
echo "  curl http://localhost:8443/api/stats"
echo "  python3 /opt/hbv-sentinel/sentinel-collector.py --generate-key"
echo ""
