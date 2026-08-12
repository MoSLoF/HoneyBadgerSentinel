# 🦡 HoneyBadger Sentinel — Installation Checklist

**CyberShield 2026 — Pre-Demo Setup**
**Version:** 1.1.2 (hardened production baseline)

---

## ☑️ Pre-installation checklist

### Infrastructure ready?

- [ ] Collector host (`<COLLECTOR_HOST>`) — online, DNS or hosts-file entry resolvable
- [ ] Reverse-proxy TLS certificate ready (public CA, or private CA whose bundle will be distributed to agents as `HBV_TLS_CA_BUNDLE`)
- [ ] Agent hosts online and network-reachable to the reverse proxy
- [ ] All Sentinel files downloaded to a working directory on your workstation

### Understand the topology

The **only** production path is a TLS-terminating reverse proxy in front of a loopback-bound collector. Read `DEPLOYMENT-GUIDE.md` if that phrase is new — none of the steps below make sense out of that context.

---

## 📋 Installation order

### Step 1: Install collector ⭐ (5 minutes)

**On the collector host:**

```bash
scp sentinel-collector.py install-collector.sh <user>@<COLLECTOR_HOST>:~
ssh <user>@<COLLECTOR_HOST>

chmod +x install-collector.sh
sudo ./install-collector.sh
```

**Verify (loopback, no auth):**

```bash
curl http://127.0.0.1:8443/health
```

**Expected result:**

```json
{"status":"healthy","timestamp":1755000000,"version":"1.1.2"}
```

**Grab the generated API key — you'll need it for every agent:**

```bash
sudo cat /etc/hbv-sentinel/api.key
```

**Verify authenticated access:**

```bash
curl -H "X-API-Key: $(sudo cat /etc/hbv-sentinel/api.key)" \
     http://127.0.0.1:8443/api/stats
```

✅ **Checkpoint:** Collector is up on loopback, generated key present.

---

### Step 2: Stand up the TLS reverse proxy (10 minutes)

Set up nginx / caddy / traefik on the collector host to terminate TLS on the LAN interface and forward to `127.0.0.1:8443`. Full config examples in `DEPLOYMENT-GUIDE.md`.

**Verify from another host:**

```bash
curl -H "X-API-Key: <the-key>" https://<COLLECTOR_HOST>/api/stats
```

**Expected:** Same JSON payload as the loopback call in Step 1.

✅ **Checkpoint:** Proxy forwards HTTPS → loopback correctly and the key still works.

---

### Step 3: Install a Linux agent (3 minutes per host)

**On the agent host (e.g. NAS at `<NAS_IP>`):**

```bash
scp sentinel-agent-linux.py install-agent-linux.sh <user>@<NAS_IP>:~
ssh <user>@<NAS_IP>

chmod +x install-agent-linux.sh
sudo ./install-agent-linux.sh
```

**Configure the agent:**

```bash
sudo tee -a /etc/hbv-sentinel/agent.env <<'EOF'
HBV_COLLECTOR_URL=https://<COLLECTOR_HOST>/api/beacon
HBV_API_KEY=<paste the key from Step 1>
# Uncomment if your collector uses a private-CA TLS cert:
# HBV_TLS_CA_BUNDLE=/etc/hbv-sentinel/ca.pem
EOF

sudo systemctl restart hbv-sentinel
sudo journalctl -u hbv-sentinel -f
# → Expect: "Beacon transmitted successfully"
```

**Verify from the collector or any authenticated host:**

```bash
curl -H "X-API-Key: <the-key>" https://<COLLECTOR_HOST>/api/agents | jq
```

**Expected result:**

```json
{
  "agents": [
    {"agent_id": "nas-server", "status": "online", "total_beacons": 2}
  ]
}
```

✅ **Checkpoint:** Linux agent appearing in `/api/agents`.

---

### Step 4: Install a Windows agent (3 minutes per host)

**On the Windows host (PowerShell 7 as Administrator):**

```powershell
Copy-Item Sentinel-Agent-Windows.ps1 C:\HBV\

# Set machine-scoped environment variables so the scheduled task inherits them.
[Environment]::SetEnvironmentVariable(
    "HBV_COLLECTOR_URL", "https://<COLLECTOR_HOST>/api/beacon", "Machine")
[Environment]::SetEnvironmentVariable(
    "HBV_API_KEY", "<paste the key>", "Machine")

# If your collector uses a private CA, import the CA into
# "Local Machine → Trusted Root Certification Authorities" before proceeding.

cd C:\HBV
.\Sentinel-Agent-Windows.ps1 -Test          # confirm metrics collection
.\Sentinel-Agent-Windows.ps1 -Install       # register scheduled task
Start-ScheduledTask -TaskName "HoneyBadger-Sentinel"
Get-Content "$env:TEMP\HBV-Sentinel.log" -Tail 20
# → Expect: "Beacon transmitted successfully"
```

**If you see "Refusing to send beacon over plaintext HTTP":** your `HBV_COLLECTOR_URL` starts with `http://`. Change it to `https://…`.

✅ **Checkpoint:** Windows agent beaconing.

---

### Step 5: Repeat Steps 3 or 4 for the remaining hosts

Repeat as needed for each additional Linux or Windows agent host.

---

## 🎯 Final verification

### Every agent online?

```bash
curl -H "X-API-Key: <key>" https://<COLLECTOR_HOST>/api/agents | \
    jq '.agents[] | {agent_id, status, total_beacons}'
```

**Expected output (one line per agent):**

```json
{"agent_id":"nas-server","status":"online","total_beacons":120}
{"agent_id":"orangepi","status":"online","total_beacons":120}
{"agent_id":"WIN-WORKSTATION","status":"online","total_beacons":120}
{"agent_id":"G16","status":"online","total_beacons":120}
```

### Recent beacon volume?

```bash
curl -H "X-API-Key: <key>" https://<COLLECTOR_HOST>/api/stats | \
    jq '.beacons.last_hour'
```

**Expected:** roughly `agents × 120` for a 30-second interval.

### Dashboard reachable? (only if opted in)

The interactive dashboard at `GET /` is DISABLED by default. If you enabled it (`HBV_ENABLE_DASHBOARD=true` in `collector.env`), open `https://<COLLECTOR_HOST>/` in a browser. Paste the key into the field labeled "API key" and click "Use key." Stats populate; the key stays in `sessionStorage` for the tab.

If you did NOT enable the dashboard, `GET /` returns 404 by design and this step does not apply.

---

## 🚨 Troubleshooting checklist

### Agent not appearing?

```bash
# On the agent host
systemctl status hbv-sentinel                    # Linux
Get-ScheduledTask HoneyBadger-Sentinel           # Windows

# Agent logs
journalctl -u hbv-sentinel -n 100                # Linux
Get-Content $env:TEMP\HBV-Sentinel.log -Tail 50  # Windows

# Reachability
curl -k -H "X-API-Key: <key>" https://<COLLECTOR_HOST>/health
```

### 401 Unauthorized

Keys don't match. Re-copy the value from `/etc/hbv-sentinel/api.key` on the collector to the agent's `HBV_API_KEY`.

### 503 Service Unavailable on beacon submissions

Collector persistence is failing (disk full, permissions, corrupt SQLite). Check `journalctl -u hbv-sentinel-collector -n 100`. Agents retry automatically — no beacon is lost.

### High beacon queue on an agent

```bash
ls -la /tmp/hbv-sentinel-queue/                  # Linux
Get-ChildItem "$env:TEMP\HBV-Sentinel-Queue"     # Windows

sudo systemctl restart hbv-sentinel              # Linux
Restart-ScheduledTask -TaskName "HoneyBadger-Sentinel"   # Windows
```

---

## 📊 Pre-demo health check (24 hours before)

```bash
KEY=$(sudo cat /etc/hbv-sentinel/api.key)
BASE=https://<COLLECTOR_HOST>

# All agents online?
curl -H "X-API-Key: $KEY" $BASE/api/agents | \
    jq '.agents[] | select(.status == "online") | .agent_id'

# Recent beacon volume?
curl -H "X-API-Key: $KEY" $BASE/api/stats | jq '.beacons.last_hour'

# Any active alerts?
curl -H "X-API-Key: $KEY" $BASE/api/alerts | jq '.alerts | length'

# Database size reasonable?
ssh <user>@<COLLECTOR_HOST> "ls -lh /opt/hbv-sentinel/sentinel.db"

# Services set to auto-start on boot?
ssh <user>@<COLLECTOR_HOST> "systemctl is-enabled hbv-sentinel-collector"
ssh <user>@<NAS_IP>         "systemctl is-enabled hbv-sentinel"
```

---

## ✅ Installation complete

**Total installation time:** ~30 minutes (collector + proxy + 4 agents).

**System status:**

- ✅ Collector on loopback with hardened defaults
- ✅ Reverse proxy terminating TLS on the LAN
- ✅ Agents beaconing over HTTPS with matching API key
- ✅ Dashboard reachable; ships no secret
- ✅ FastAPI `/docs`, `/redoc`, `/openapi.json` disabled
- ✅ Auto-start enabled

**Ready for CyberShield 2026.** 🦡

═══════════════════════════════════════════════════════════
🦡 HoneyBadger Vanguard 2.0 — Sentinel Monitoring System
CyberShield 2026 — Infrastructure Resilience Demonstration
═══════════════════════════════════════════════════════════
