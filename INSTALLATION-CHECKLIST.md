# 🦡 HoneyBadger Sentinel - Quick Installation Checklist

**CyberShield 2026 - Pre-Demo Setup**

---

## ☑️ Pre-Installation Checklist

### Infrastructure Ready?
- [ ] Collector Server (<COLLECTOR_IP>) - Online and accessible
- [ ] NAS (<NAS_IP>) - Online and accessible  
- [ ] Windows Workstation (Workstation) - Online and accessible
- [ ] OrangePi 6P (<AGENT_IP>) - Online and accessible
- [ ] G16 Laptop (<AGENT_IP>) - Online and accessible
- [ ] Network connectivity between all devices verified

### Files Ready?
- [ ] All 7 Sentinel files downloaded
- [ ] Files uploaded to appropriate devices

---

## 📋 Installation Order

### Step 1: Install Collector First ⭐ (5 minutes)

**On Collector (<COLLECTOR_IP>):**

```bash
# Upload files
scp sentinel-collector.py install-collector.sh <user>@<COLLECTOR_IP>:~

# SSH to Collector
ssh <user>@<COLLECTOR_IP>

# Install
chmod +x install-collector.sh
sudo ./install-collector.sh

# Verify
curl http://localhost:8443/api/stats | jq
```

**Expected Result:**
```json
{
  "agents": {"total": 0, "online": 0, "offline": 0},
  "beacons": {"total": 0, "last_hour": 0},
  "alerts": {"unresolved": 0}
}
```

✅ **Checkpoint:** Dashboard accessible at http://<COLLECTOR_IP>:8443

---

### Step 2: Install NAS Agent (3 minutes)

**On NAS (<NAS_IP>):**

```bash
# Upload files
scp sentinel-agent-linux.py install-agent-linux.sh <user>@<NAS_IP>:~

# SSH to NAS
ssh <user>@<NAS_IP>

# Install
chmod +x install-agent-linux.sh
sudo ./install-agent-linux.sh
```

**Wait 1 minute, then verify:**
```bash
# From any machine
curl http://<COLLECTOR_IP>:8443/api/agents | jq
```

**Expected Result:**
```json
{
  "agents": [
    {
      "agent_id": "nas-server",
      "status": "online",
      "total_beacons": 2
    }
  ]
}
```

✅ **Checkpoint:** NAS agent appearing in collector

---

### Step 3: Install OrangePi Agent (3 minutes)

**On OrangePi 6P (<AGENT_IP>):**

```bash
# Upload files
scp sentinel-agent-linux.py install-agent-linux.sh <user>@<AGENT_IP>:~

# SSH to OrangePi
ssh <user>@<AGENT_IP>

# Install
chmod +x install-agent-linux.sh
sudo ./install-agent-linux.sh
```

✅ **Checkpoint:** OrangePi agent online

---

### Step 4: Install Windows TUF Agent (3 minutes)

**On Windows Workstation (Local Workstation):**

```powershell
# Copy file to C:\HBV\
Copy-Item Sentinel-Agent-Windows.ps1 C:\HBV\

# Open PowerShell as Administrator
cd C:\HBV

# Test metrics collection
.\Sentinel-Agent-Windows.ps1 -Test

# Install as scheduled task
.\Sentinel-Agent-Windows.ps1 -Install

# Start task
Start-ScheduledTask -TaskName "HoneyBadger-Sentinel"
```

**Verify:**
```powershell
# Check task is running
Get-ScheduledTask -TaskName "HoneyBadger-Sentinel"

# Check log
Get-Content "$env:TEMP\HBV-Sentinel.log" -Tail 20
```

✅ **Checkpoint:** TUF agent beaconing

---

### Step 5: Install Windows G16 Agent (Optional) (3 minutes)

**On G16 Laptop (<AGENT_IP>):**

Same process as Step 4.

✅ **Checkpoint:** G16 agent online

---

## 🎯 Final Verification (2 minutes)

### Check All Agents Online

```bash
curl http://<COLLECTOR_IP>:8443/api/agents | jq '.agents[] | {agent_id, status, total_beacons}'
```

**Expected Output:**
```json
{"agent_id": "nas-server", "status": "online", "total_beacons": 120}
{"agent_id": "linux-agent-01", "status": "online", "total_beacons": 120}
{"agent_id": "WIN-WORKSTATION", "status": "online", "total_beacons": 120}
{"agent_id": "G16", "status": "online", "total_beacons": 120}
```

### Check Recent Beacon Activity

```bash
curl http://<COLLECTOR_IP>:8443/api/stats | jq '.beacons.last_hour'
```

**Expected:** Should show number of beacons in last hour (agents * 120)

### Test Dashboard

Open browser: http://<COLLECTOR_IP>:8443

**Should show:**
- Agents online count
- Recent beacon activity
- Alert status

---

## 🚨 Troubleshooting Checklist

### Agent Not Appearing?

```bash
# On agent machine
systemctl status hbv-sentinel                    # Linux
Get-ScheduledTask HoneyBadger-Sentinel           # Windows

# Check logs
journalctl -u hbv-sentinel -f                    # Linux
Get-Content $env:TEMP\HBV-Sentinel.log -Tail 50  # Windows

# Test connectivity
ping <COLLECTOR_IP>
curl http://<COLLECTOR_IP>:8443/health
```

### No Beacons Received?

```bash
# Check collector is running
ssh <user>@<COLLECTOR_IP>
systemctl status hbv-sentinel-collector
journalctl -u hbv-sentinel-collector -f

# Check firewall
sudo ufw status
```

### High Latency?

```bash
# Check beacon queue on agent
ls -la /tmp/hbv-sentinel-queue/                  # Linux
ls "$env:TEMP\HBV-Sentinel-Queue"                # Windows

# Restart agent to flush queue
systemctl restart hbv-sentinel                   # Linux
Restart-ScheduledTask HoneyBadger-Sentinel       # Windows
```

---

## 📊 Pre-Demo Health Check

**Run 24 hours before CyberShield 2026:**

```bash
# 1. All agents online?
curl http://<COLLECTOR_IP>:8443/api/agents | jq '.agents[] | select(.status == "online") | .agent_id'

# 2. Recent beacon activity?
curl http://<COLLECTOR_IP>:8443/api/stats | jq '.beacons.last_hour'

# 3. Any active alerts?
curl http://<COLLECTOR_IP>:8443/api/alerts | jq '.alerts | length'

# 4. Database size reasonable?
ssh <user>@<COLLECTOR_IP> "ls -lh /opt/hbv-sentinel/sentinel.db"

# 5. Services auto-start on boot?
ssh <user>@<COLLECTOR_IP> "systemctl is-enabled hbv-sentinel-collector"
ssh <user>@<NAS_IP> "systemctl is-enabled hbv-sentinel"
```

---

## 🎤 Demo Day Talking Points

### Opening (30 seconds)
"I've built HoneyBadger Sentinel, a C2-style infrastructure monitoring system that demonstrates how offensive security design patterns can be applied to legitimate defensive operations."

### Technical Overview (1 minute)
"The system uses a beacon-based architecture similar to Cobalt Strike or Metasploit, but instead of offensive payloads, my agents beacon system telemetry every 30 seconds. This includes CPU, memory, disk usage, GPU temperatures, and even RAID array health from my NAS."

### Resilience Demo (30 seconds)
"Watch what happens when I disconnect the collector... [show queued beacons] ...and when I bring it back online, all queued metrics automatically catch up. No data loss."

### Multi-Platform (30 seconds)
"I've got agents running on Windows via PowerShell, Linux via Python, and they all feed into this unified FastAPI collector with SQLite time-series storage."

### Real-World Application (30 seconds)
"This isn't just a toy - it's production-grade infrastructure monitoring. I've got automated alerting, RESTful API, and I'm ready to integrate vehicle telemetry from my Jeep's CAN bus through the OrangePi KITT system."

---

## ✅ Installation Complete!

**Total Installation Time:** ~20-30 minutes

**System Status:**
- ✅ Collector running on Collector
- ✅ Agents beaconing from all devices
- ✅ Dashboard accessible
- ✅ Alerts configured
- ✅ Auto-start enabled

**Ready for CyberShield 2026!** 🦡

═══════════════════════════════════════════════════════════
🦡 HoneyBadger Vanguard 2.0 - Sentinel Monitoring System
CyberShield 2026 - Infrastructure Resilience Demonstration
═══════════════════════════════════════════════════════════
