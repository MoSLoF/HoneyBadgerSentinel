# 🦡 HoneyBadger Sentinel

**C2-Style Infrastructure Monitoring System for CyberShield 2026**

A distributed monitoring system with Command & Control beacon architecture designed to showcase advanced red team infrastructure techniques in a legitimate blue team application.

**Version:** 1.1.0

---

## 🎯 Features

✅ **C2-Style Beacon Architecture** - Agents beacon metrics like offensive security implants
✅ **Offline Resilience** - Agents queue beacons when collector unavailable
✅ **Multi-Platform** - Windows (PowerShell) and Linux (Python) agents
✅ **Real-Time Monitoring** - 30-second beacon intervals with time-series storage
✅ **Alert Engine** - Automated threshold-based alerting
✅ **Web Dashboard** - Real-time visualization and API access
✅ **Custom Integrations** - RAID health, GPU temps, vehicle telemetry ready

### v1.1.0 Production Features

✅ **API Key Authentication** - Optional token-based auth for secure deployments
✅ **Rate Limiting** - Per-IP request throttling to prevent abuse
✅ **Input Validation** - Pydantic models for all API requests
✅ **Prometheus Metrics** - `/metrics` endpoint for monitoring integration
✅ **Environment Config** - All settings via environment variables
✅ **Graceful Shutdown** - Clean signal handling for zero-downtime restarts
✅ **Log Rotation** - Logrotate configuration included  

---

## 📦 Components

```
honeybadger-sentinel/
├── sentinel-collector.py            # Central FastAPI collector
├── sentinel-agent-linux.py          # Python agent for Linux
├── Sentinel-Agent-Windows.ps1       # PowerShell agent for Windows
├── install-collector.sh             # Collector installation script
├── install-agent-linux.sh           # Linux agent installation script
├── requirements.txt                 # Python dependencies
├── .env.example                     # Environment configuration template
├── config/
│   └── logrotate.conf               # Log rotation configuration
├── scripts/
│   └── backup-db.sh                 # Database backup script
├── tests/
│   └── test_collector.py            # Unit tests
├── DEPLOYMENT-GUIDE.md              # Complete deployment guide
└── README.md                        # This file
```

---

## 🚀 Quick Start

### 1. Install Collector (iHBV-AI: 192.168.36.241)

```bash
ssh honeybadger@192.168.36.241
sudo ./install-collector.sh
```

Dashboard: http://192.168.36.241:8443

### 2. Install Linux Agent (NAS: 192.168.36.243)

```bash
ssh honeybadger@192.168.36.243
sudo ./install-agent-linux.sh
```

### 3. Install Windows Agent (iHBV-TUF)

```powershell
.\Sentinel-Agent-Windows.ps1 -Install
Start-ScheduledTask -TaskName "HoneyBadger-Sentinel"
```

### 4. Verify

```bash
curl http://192.168.36.241:8443/api/stats | jq
```

---

## 📊 System Architecture

```
┌──────────────────────────────────────────────┐
│  Central Collector (iHBV-AI R720)            │
│  • FastAPI HTTP Server (Port 8443)           │
│  • SQLite Time-Series Database               │
│  • Alert Engine                              │
│  • Web Dashboard                             │
└──────────────────────────────────────────────┘
              ▲
              │ HTTP Beacons
              │
    ┌─────────┼─────────┬─────────┐
    │         │         │         │
  ┌─▼─┐     ┌─▼─┐     ┌─▼─┐     ┌─▼─┐
  │NAS│     │TUF│     │OPi│     │G16│
  │.243│    │.1  │    │.242│    │.240│
  └───┘     └───┘     └───┘     └───┘
  Linux     Windows   Linux     Windows
```

---

## 🔧 Configuration

All configuration is via environment variables. Set them in:
- `/etc/hbv-sentinel/collector.env` (collector)
- `/etc/hbv-sentinel/agent.env` (Linux agents)
- System environment (Windows agents)

### Key Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `HBV_PORT` | 8443 | Collector listen port |
| `HBV_API_KEY` | (generated) | API authentication key |
| `HBV_API_KEY_REQUIRED` | false | Enable authentication |
| `HBV_BEACON_INTERVAL` | 30 | Agent beacon interval (seconds) |
| `HBV_COLLECTOR_URL` | http://192.168.36.241:8443/api/beacon | Collector endpoint |
| `HBV_RETENTION_DAYS` | 30 | Data retention period |
| `HBV_LOG_LEVEL` | INFO | Logging level |

See `.env.example` for full list.

### Alert Thresholds

```bash
HBV_ALERT_CPU=90        # CPU usage %
HBV_ALERT_MEMORY=90     # Memory usage %
HBV_ALERT_DISK=90       # Disk usage %
HBV_ALERT_GPU_TEMP=85   # GPU temperature °C
```

---

## 📡 Metrics Collected

### All Platforms
- CPU usage (%)
- Memory usage (MB, %)
- Disk usage (GB, %)
- Network statistics
- System uptime

### Windows-Specific
- GPU utilization (NVIDIA)
- GPU temperature
- Service status (Ollama, Docker)

### Linux-Specific
- CPU temperature
- RAID array status (NAS)
- Load average

---

## 🚨 Alert Types

1. **CPU High** - CPU usage > 90%
2. **Memory High** - Memory usage > 90%
3. **Disk High** - Disk usage > 90%
4. **GPU Temperature** - GPU temp > 85°C
5. **RAID Degraded** - RAID array unhealthy

---

## 📊 API Endpoints

```bash
# Statistics
GET  /api/stats

# Agents
GET  /api/agents

# Beacons
GET  /api/beacons/latest
GET  /api/beacons/{agent_id}

# Alerts
GET  /api/alerts

# Beacon Submission (Agents)
POST /api/beacon

# Health & Monitoring
GET  /health              # Health check
GET  /metrics             # Prometheus metrics
```

**API Docs:** http://192.168.36.241:8443/docs

### Prometheus Integration

Add to your `prometheus.yml`:
```yaml
scrape_configs:
  - job_name: 'hbv-sentinel'
    static_configs:
      - targets: ['192.168.36.241:8443']
    metrics_path: '/metrics'
```

---

## 🎯 CyberShield 2026 Demo Value

### Judge Appeal
- "Built a legitimate C2-style monitoring system"
- "Demonstrates offensive security architecture in defensive context"
- "Distributed sensor network with autonomous agents"
- "Production-grade infrastructure with proper logging and alerting"

### Technical Highlights
✅ Beacon/callback architecture (like Cobalt Strike)  
✅ Offline resilience (queued beacons)  
✅ Time-series metrics storage  
✅ RESTful API design  
✅ Cross-platform agent deployment  
✅ Automated alert generation  

---

## 🛠️ Management Commands

### Collector

```bash
# Status
systemctl status hbv-sentinel-collector

# Logs
journalctl -u hbv-sentinel-collector -f

# Restart
systemctl restart hbv-sentinel-collector
```

### Linux Agents

```bash
# Status
systemctl status hbv-sentinel

# Logs
journalctl -u hbv-sentinel -f

# Restart
systemctl restart hbv-sentinel
```

### Windows Agents

```powershell
# Check task
Get-ScheduledTask -TaskName "HoneyBadger-Sentinel"

# View logs
Get-Content "$env:TEMP\HBV-Sentinel.log" -Tail 50

# Restart
Stop-ScheduledTask -TaskName "HoneyBadger-Sentinel"
Start-ScheduledTask -TaskName "HoneyBadger-Sentinel"
```

---

## 🔐 Security Features (v1.1.0)

### API Key Authentication

```bash
# Generate an API key
python3 sentinel-collector.py --generate-key

# Configure collector (/etc/hbv-sentinel/collector.env)
HBV_API_KEY_REQUIRED=true
HBV_API_KEY=your-generated-key-here

# Configure agents (/etc/hbv-sentinel/agent.env)
HBV_API_KEY=your-generated-key-here

# Restart services
systemctl restart hbv-sentinel-collector
systemctl restart hbv-sentinel
```

### Rate Limiting

Default: 100 requests per IP per 60 seconds. Configure via:
```bash
HBV_RATE_LIMIT_REQUESTS=100
HBV_RATE_LIMIT_WINDOW=60
```

### CORS Configuration

Restrict allowed origins (comma-separated):
```bash
HBV_ALLOWED_ORIGINS=http://192.168.36.241:8443,http://localhost:8443
```

### Additional Hardening (Optional)

- Enable HTTPS with TLS certificates (reverse proxy recommended)
- Add firewall rules to restrict access
- Use VPN for remote agent connections

---

## 🔄 Backup & Maintenance

### Database Backup

```bash
# Manual backup
sudo /opt/hbv-sentinel/scripts/backup-db.sh

# Setup daily cron (2am)
echo "0 2 * * * /opt/hbv-sentinel/scripts/backup-db.sh --cron" | sudo crontab -
```

Backups stored in `/opt/hbv-sentinel/backups/` (7-day retention).

### Log Rotation

```bash
# Install logrotate config
sudo cp config/logrotate.conf /etc/logrotate.d/hbv-sentinel
```

### Running Tests

```bash
pip install pytest
pytest tests/ -v
```

---

## 🚀 Future Enhancements

### Phase 2 (Optional)
- [ ] MQTT integration alongside HTTP
- [ ] Grafana dashboard with historical graphs
- [ ] Email/SMS alert notifications
- [ ] Vehicle telemetry (KITT OBD2/CAN bus)
- [ ] Geographic topology map
- [ ] Agent command tasking

---

## 📝 Files Overview

| File | Purpose | Deploy To |
|------|---------|-----------|
| `sentinel-collector.py` | Central collector server | iHBV-AI (192.168.36.241) |
| `sentinel-agent-linux.py` | Linux monitoring agent | NAS, OrangePi, etc. |
| `Sentinel-Agent-Windows.ps1` | Windows monitoring agent | iHBV-TUF, G16 |
| `install-collector.sh` | Collector installation | iHBV-AI |
| `install-agent-linux.sh` | Linux agent installation | Linux devices |
| `DEPLOYMENT-GUIDE.md` | Complete setup guide | Reference |

---

## 🆘 Troubleshooting

### Agents Not Connecting
```bash
# Check collector is running
systemctl status hbv-sentinel-collector

# Test endpoint
curl http://192.168.36.241:8443/health

# Check network
ping 192.168.36.241
```

### No Beacons Received
```bash
# Check agent service
systemctl status hbv-sentinel  # Linux
Get-ScheduledTask HoneyBadger-Sentinel  # Windows

# Check agent logs
journalctl -u hbv-sentinel -f  # Linux
Get-Content $env:TEMP\HBV-Sentinel.log -Tail 50  # Windows
```

### High Queue on Agents
- Collector may be offline
- Network connectivity issues
- Agents will automatically catch up when connection restored

---

## 📖 Documentation

- **Full Deployment Guide:** `DEPLOYMENT-GUIDE.md`
- **API Documentation:** http://192.168.36.241:8443/docs
- **Dashboard:** http://192.168.36.241:8443

---

## 🦡 About

Created for **CyberShield 2026** demonstration by HoneyBadger.

Demonstrates advanced infrastructure monitoring using offensive security design patterns in a legitimate defensive application.

**System is designed for autonomous operation - set it and forget it!**

═══════════════════════════════════════════════════════════  
🦡 HoneyBadger Vanguard 2.0 - Infrastructure Monitoring  
CyberShield 2026 - 198 Days Remaining  
═══════════════════════════════════════════════════════════
