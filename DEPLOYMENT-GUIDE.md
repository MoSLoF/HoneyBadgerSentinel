# 🦡 HoneyBadger Sentinel - Deployment Guide

**C2-Style Infrastructure Monitoring for CyberShield 2026**

---

## 📋 System Overview

HoneyBadger Sentinel is a distributed monitoring system with C2-style beacon architecture:

```
┌─────────────────────────────────────────────────┐
│  Central Collector (iHBV-AI: 192.168.36.241)    │
│  ├─ FastAPI HTTP Server (Port 8443)             │
│  ├─ SQLite Time-Series Database                 │
│  ├─ Alert Engine                                │
│  └─ Web Dashboard                               │
└─────────────────────────────────────────────────┘
              ▲
              │ HTTP Beacons (30s interval)
              │
    ┌─────────┼─────────┬─────────┬─────────┐
    │         │         │         │         │
  ┌─▼─┐     ┌─▼─┐     ┌─▼─┐     ┌─▼─┐     ┌─▼─┐
  │NAS│     │R720│    │OPi│     │TUF│     │G16│
  └───┘     └────┘    └───┘     └───┘     └───┘
  Linux     Linux     Linux     Windows   Windows
  Agent     Agent     Agent     Agent     Agent
```

---

## 🚀 Quick Start (5 Minutes)

### Step 1: Install Collector (iHBV-AI R720)

```bash
# SSH to iHBV-AI
ssh honeybadger@192.168.36.241

# Upload files
scp sentinel-collector.py install-collector.sh honeybadger@192.168.36.241:~

# Install
chmod +x install-collector.sh
sudo ./install-collector.sh
```

### Step 2: Install Linux Agents (NAS, OrangePi)

```bash
# SSH to NAS
ssh honeybadger@192.168.36.243

# Upload files
scp sentinel-agent-linux.py install-agent-linux.sh honeybadger@192.168.36.243:~

# Install
chmod +x install-agent-linux.sh
sudo ./install-agent-linux.sh
```

### Step 3: Install Windows Agents (TUF, G16)

```powershell
# On iHBV-TUF
Copy-Item Sentinel-Agent-Windows.ps1 C:\HBV\

# Install as scheduled task
cd C:\HBV
.\Sentinel-Agent-Windows.ps1 -Install

# Start agent
Start-ScheduledTask -TaskName "HoneyBadger-Sentinel"
```

### Step 4: Verify Operation

```bash
# Check collector stats
curl http://192.168.36.241:8443/api/stats | jq

# Check active agents
curl http://192.168.36.241:8443/api/agents | jq
```

---

## 📦 Detailed Installation

### Prerequisites

**Collector (iHBV-AI):**
- Ubuntu/Debian Linux
- Python 3.8+
- pip3
- Root access

**Linux Agents:**
- Any Linux distribution
- Python 3.8+
- pip3
- Root access

**Windows Agents:**
- Windows 10/11
- PowerShell 7+
- Administrator access

---

## 🔧 Configuration

### Collector Configuration

Edit `/opt/hbv-sentinel/sentinel-collector.py`:

```python
CONFIG = {
    "host": "0.0.0.0",        # Listen on all interfaces
    "port": 8443,             # API port
    "db_path": "/opt/hbv-sentinel/sentinel.db",
    "retention_days": 30,     # Data retention
    
    # Alert thresholds
    "alert_thresholds": {
        "cpu_percent": 90,
        "memory_percent": 90,
        "disk_percent": 90,
        "gpu_temp_c": 85
    }
}
```

### Linux Agent Configuration

Edit `/opt/hbv-sentinel/sentinel-agent-linux.py`:

```python
CONFIG = {
    "agent_id": socket.gethostname(),
    "api_endpoint": "http://192.168.36.241:8443/api/beacon",
    "beacon_interval": 30,    # Beacon every 30 seconds
    "max_retries": 3,
    "queue_path": "/tmp/hbv-sentinel-queue",
}
```

### Windows Agent Configuration

Edit `Sentinel-Agent-Windows.ps1`:

```powershell
$script:Config = @{
    AgentID = $env:COMPUTERNAME
    APIEndpoint = "http://192.168.36.241:8443/api/beacon"
    BeaconInterval = 30       # Beacon every 30 seconds
    MaxRetries = 3
    QueuePath = "$env:TEMP\HBV-Sentinel-Queue"
}
```

---

## 📊 Dashboard & API

### Web Dashboard

Open in browser:
```
http://192.168.36.241:8443
```

Real-time view of:
- Agents online/offline
- Beacon activity
- Active alerts

### API Endpoints

```bash
# Get statistics
curl http://192.168.36.241:8443/api/stats

# List all agents
curl http://192.168.36.241:8443/api/agents

# Get latest beacons
curl http://192.168.36.241:8443/api/beacons/latest?limit=10

# Get agent-specific beacons
curl http://192.168.36.241:8443/api/beacons/iHBV-TUF?limit=10

# Get active alerts
curl http://192.168.36.241:8443/api/alerts
```

### API Documentation

Interactive Swagger docs:
```
http://192.168.36.241:8443/docs
```

---

## 🔍 Monitoring & Management

### Check Collector Status

```bash
# Service status
systemctl status hbv-sentinel-collector

# View logs
journalctl -u hbv-sentinel-collector -f

# Check stats
curl http://localhost:8443/api/stats | jq
```

### Check Agent Status (Linux)

```bash
# Service status
systemctl status hbv-sentinel

# View logs
journalctl -u hbv-sentinel -f

# Test metrics collection
python3 /opt/hbv-sentinel/sentinel-agent-linux.py --test
```

### Check Agent Status (Windows)

```powershell
# Check scheduled task
Get-ScheduledTask -TaskName "HoneyBadger-Sentinel"

# View logs
Get-Content "$env:TEMP\HBV-Sentinel.log" -Tail 50

# Test metrics collection
.\Sentinel-Agent-Windows.ps1 -Test
```

---

## 🚨 Alert System

### Alert Types

1. **CPU High** - CPU usage > 90%
2. **Memory High** - Memory usage > 90%
3. **Disk High** - Disk usage > 90%
4. **GPU Temperature** - GPU temp > 85°C
5. **RAID Degraded** - RAID array not healthy

### Viewing Alerts

```bash
# Get all unresolved alerts
curl http://192.168.36.241:8443/api/alerts | jq

# Filter by agent
curl http://192.168.36.241:8443/api/alerts | jq '.alerts[] | select(.agent_id == "iHBV-NAS-TT")'
```

---

## 🔐 Security Considerations

### Current Implementation
- HTTP (unencrypted)
- No authentication
- Suitable for isolated lab network

### Production Hardening (Optional)
1. Enable HTTPS with TLS certificates
2. Add API key authentication
3. Implement rate limiting
4. Firewall rules (allow only trusted IPs)

---

## 🎯 CyberShield 2026 Demo Tips

### Pre-Demo Checklist

```bash
# 1. Verify all agents online
curl http://192.168.36.241:8443/api/agents | jq '.agents[] | select(.status == "online")'

# 2. Check recent beacon activity
curl http://192.168.36.241:8443/api/stats | jq '.beacons.last_hour'

# 3. Clear old alerts
# (Manual via database if needed)

# 4. Test dashboard
curl http://192.168.36.241:8443/health
```

### Demo Talking Points

✅ **"C2-Style Infrastructure Monitoring"**
- Beacon-based architecture similar to red team C2 frameworks
- Distributed agent network
- Centralized command and control

✅ **"Offline Resilience"**
- Agents queue beacons when collector is offline
- Automatic catch-up when connectivity restored
- No data loss during network outages

✅ **"Real-Time Telemetry"**
- 30-second beacon intervals
- Time-series metrics storage
- Historical trend analysis

✅ **"Multi-Platform Support"**
- Windows PowerShell agents
- Linux Python agents
- Unified data collection

✅ **"Custom Integrations"**
- NAS RAID health monitoring
- GPU temperature monitoring
- Vehicle telemetry (KITT) ready

---

## 🛠️ Troubleshooting

### Agents Not Connecting

```bash
# Check network connectivity
ping 192.168.36.241

# Check collector is running
systemctl status hbv-sentinel-collector

# Check firewall
sudo ufw status
sudo firewall-cmd --list-all

# Test API endpoint
curl http://192.168.36.241:8443/health
```

### High Beacon Queue on Agent

```bash
# Check queue directory
ls -lah /tmp/hbv-sentinel-queue/

# Check collector logs
journalctl -u hbv-sentinel-collector -n 100

# Manually flush queue by restarting agent
systemctl restart hbv-sentinel
```

### Database Issues

```bash
# Check database file
ls -lah /opt/hbv-sentinel/sentinel.db

# Backup database
cp /opt/hbv-sentinel/sentinel.db /opt/hbv-sentinel/sentinel.db.backup

# Reset database (WARNING: Deletes all data)
systemctl stop hbv-sentinel-collector
rm /opt/hbv-sentinel/sentinel.db
systemctl start hbv-sentinel-collector
```

---

## 📈 Performance Tuning

### Adjust Beacon Intervals

**For Demo (More Frequent):**
```python
"beacon_interval": 10  # 10 seconds - more responsive
```

**For Production (Less Frequent):**
```python
"beacon_interval": 60  # 60 seconds - less overhead
```

### Database Maintenance

```bash
# Clean up old data (keeps last 7 days)
sqlite3 /opt/hbv-sentinel/sentinel.db "DELETE FROM beacons WHERE timestamp < strftime('%s', 'now', '-7 days');"

# Vacuum database
sqlite3 /opt/hbv-sentinel/sentinel.db "VACUUM;"
```

---

## 🚀 Next Steps

### Phase 2 Enhancements (Optional)

1. **MQTT Integration**
   - Add Mosquitto broker
   - Enable MQTT beacons alongside HTTP
   - Pub/sub alert system

2. **Advanced Dashboard**
   - Grafana integration
   - Real-time graphs
   - Historical charts
   - Topology visualization

3. **Vehicle Integration**
   - OBD2/CAN bus metrics from KITT
   - GPS location tracking
   - Speed/RPM telemetry

4. **Alert Notifications**
   - Email notifications
   - SMS alerts
   - Slack/Discord webhooks

---

## 📝 Additional Resources

- Collector logs: `/var/log/hbv-sentinel/`
- Agent logs (Linux): `/var/log/hbv-sentinel.log`
- Agent logs (Windows): `%TEMP%\HBV-Sentinel.log`
- Database: `/opt/hbv-sentinel/sentinel.db`
- API docs: `http://192.168.36.241:8443/docs`

---

## 🦡 Support

For issues during CyberShield 2026:
1. Check logs first
2. Verify network connectivity
3. Restart services if needed
4. Check GitHub issues (if open-sourced)

**System is designed for autonomous operation - set it and forget it!**

═══════════════════════════════════════════════════════════
🦡 HoneyBadger Vanguard 2.0 - Sentinel Monitoring System
CyberShield 2026 - Infrastructure Resilience Demonstration
═══════════════════════════════════════════════════════════
