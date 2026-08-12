# 🦡 HoneyBadger Sentinel — Deployment Guide

**C2-Style Infrastructure Monitoring for CyberShield 2026**
**Version:** 1.1.2 (hardened production baseline)

---

## 📋 System overview

Sentinel is a distributed monitoring system with a C2-style beacon architecture. The **only** supported production topology puts a TLS-terminating reverse proxy in front of a loopback-bound collector:

```
                     ┌────────────────────────────────┐
                     │  Reverse proxy (nginx / caddy) │
                     │  https://collector.example:443 │
                     │  ─ terminates TLS              │
                     │  ─ forwards → 127.0.0.1:8443   │
                     └───────────────┬────────────────┘
                                     │ loopback only
                     ┌───────────────▼────────────────┐
                     │  sentinel-collector (loopback) │
                     │  ─ HBV_API_KEY_REQUIRED=true   │
                     │  ─ SQLite time-series DB       │
                     │  ─ alert engine                │
                     └────────────────────────────────┘
                                     ▲
                       HTTPS + X-API-Key + freshness
                                     │
        ┌────────────────┬───────────┴────────────┬────────────────┐
        │                │                        │                │
    ┌───▼───┐        ┌───▼───┐                ┌───▼───┐        ┌───▼───┐
    │Linux  │        │Linux  │                │Windows│        │Windows│
    │ agent │        │ agent │                │ agent │        │ agent │
    └───────┘        └───────┘                └───────┘        └───────┘
```

Beacons flow **only** over HTTPS with a matching `X-API-Key`. Both agents refuse plaintext HTTP by default; the collector rejects unauthenticated requests to every telemetry endpoint.

For laboratory bring-up on a single host with everything on loopback, see the "Lab-only override" section at the bottom.

---

## 🚀 Full deployment

### Step 1: Install the collector

```bash
sudo ./install-collector.sh
```

The installer:
- Generates a stable API key and writes it to `/etc/hbv-sentinel/api.key` (mode 0600).
- Writes hardened defaults to `/etc/hbv-sentinel/collector.env` (loopback bind, auth-required, empty CORS, docs off).
- Installs `hbv-sentinel-collector.service` and starts it.

Verify the collector is up:

```bash
curl http://127.0.0.1:8443/health
# → {"status":"healthy","timestamp":…,"version":"1.1.2"}
```

Verify authenticated access works:

```bash
curl -H "X-API-Key: $(sudo cat /etc/hbv-sentinel/api.key)" \
     http://127.0.0.1:8443/api/stats
```

### Step 2: Stand up the TLS reverse proxy

Any of nginx, caddy, or traefik works. The collector never terminates TLS itself. The proxy MUST:

- Listen on the LAN address (e.g. `0.0.0.0:443`).
- Terminate TLS with a certificate the agents trust (public CA or private CA distributed to agents as `HBV_TLS_CA_BUNDLE`).
- Forward all requests to `http://127.0.0.1:8443` (loopback, no re-encryption needed since it never leaves the host).
- Preserve the `X-API-Key` header (this is the default; do not strip it).

**Do not change `HBV_HOST` on the collector.** Keeping it on `127.0.0.1` guarantees that a misconfigured or missing proxy cannot accidentally expose the collector on the LAN.

Minimal caddy example (`/etc/caddy/Caddyfile`):

```
collector.example.com {
    reverse_proxy 127.0.0.1:8443
}
```

Minimal nginx example:

```nginx
server {
    listen 443 ssl http2;
    server_name collector.example.com;
    ssl_certificate     /etc/ssl/collector.pem;
    ssl_certificate_key /etc/ssl/collector.key;

    location / {
        proxy_pass         http://127.0.0.1:8443;
        proxy_set_header   Host              $host;
        proxy_set_header   X-Real-IP         $remote_addr;
        proxy_set_header   X-Forwarded-For   $proxy_add_x_forwarded_for;
        proxy_set_header   X-Forwarded-Proto $scheme;
    }
}
```

Test the full path from another host:

```bash
curl -H "X-API-Key: <the-key>" \
     https://collector.example.com/api/stats
```

### Step 3: Install a Linux agent

```bash
sudo ./install-agent-linux.sh
```

Edit `/etc/hbv-sentinel/agent.env` and set at minimum:

```bash
HBV_COLLECTOR_URL=https://collector.example.com/api/beacon
HBV_API_KEY=<paste the key from /etc/hbv-sentinel/api.key on the collector>
# Private-CA collector? Uncomment and point at the CA bundle:
# HBV_TLS_CA_BUNDLE=/etc/hbv-sentinel/ca.pem
```

Start the agent:

```bash
sudo systemctl restart hbv-sentinel
sudo journalctl -u hbv-sentinel -f
# Expect: "Beacon transmitted successfully"
```

### Step 4: Install a Windows agent

Copy `Sentinel-Agent-Windows.ps1` to the target host, then in **PowerShell 7 as Administrator**:

```powershell
[Environment]::SetEnvironmentVariable(
    "HBV_COLLECTOR_URL", "https://collector.example.com/api/beacon", "Machine")
[Environment]::SetEnvironmentVariable(
    "HBV_API_KEY", "<paste the key>", "Machine")

# For a private-CA collector cert, import the CA into
# "Local Machine → Trusted Root Certification Authorities" first.

cd C:\HBV
.\Sentinel-Agent-Windows.ps1 -Test          # confirm metrics collection works
.\Sentinel-Agent-Windows.ps1 -Install       # register scheduled task
Start-ScheduledTask -TaskName "HoneyBadger-Sentinel"
Get-Content "$env:TEMP\HBV-Sentinel.log" -Tail 20
```

If the agent logs "Refusing to send beacon over plaintext HTTP," the `HBV_COLLECTOR_URL` value is `http://…`. Switch it to `https://…`.

### Step 5: Verify

```bash
# Are all agents visible?
curl -H "X-API-Key: <key>" https://collector.example.com/api/agents | jq

# Beacon volume in the last hour:
curl -H "X-API-Key: <key>" https://collector.example.com/api/stats | jq
```

---

## 🔧 Configuration reference

All configuration is via environment variables. The `install-collector.sh` script writes a complete `/etc/hbv-sentinel/collector.env` on first run with hardened defaults; the environment values below are what those defaults resolve to.

### Collector (`/etc/hbv-sentinel/collector.env`)

```bash
HBV_HOST=127.0.0.1
HBV_PORT=8443
HBV_API_KEY_REQUIRED=true
HBV_API_KEY=<generated by installer into /etc/hbv-sentinel/api.key>
HBV_ALLOWED_ORIGINS=            # empty by default
HBV_BEACON_MAX_SKEW=300         # ±5 min freshness + replay window
HBV_ENABLE_DOCS=false           # /docs, /redoc, /openapi.json OFF
HBV_ENABLE_DASHBOARD=false      # GET / OFF; enable only from a management origin
HBV_RATE_LIMIT_REQUESTS=100
HBV_RATE_LIMIT_WINDOW=60
HBV_DB_PATH=/opt/hbv-sentinel/sentinel.db
HBV_RETENTION_DAYS=30
```

### Linux agent (`/etc/hbv-sentinel/agent.env`)

```bash
HBV_COLLECTOR_URL=https://collector.example.com/api/beacon
HBV_API_KEY=<same value as the collector>
# HBV_TLS_CA_BUNDLE=/etc/hbv-sentinel/ca.pem    # optional, for private CA
# HBV_ALLOW_INSECURE=false                       # keep false in production
HBV_BEACON_INTERVAL=30
```

### Windows agent (machine environment variables)

```powershell
HBV_COLLECTOR_URL   = "https://collector.example.com/api/beacon"
HBV_API_KEY         = "<same value as the collector>"
# HBV_ALLOW_INSECURE = "false"                   # keep false in production
# HBV_TLS_CA_BUNDLE  = "false"                   # ONLY if you must skip verify
HBV_BEACON_INTERVAL = 30
```

---

## 📊 Dashboard & API

### Web dashboard (opt-in)

The interactive dashboard at `GET /` is DISABLED by default so the collector's only anonymous public route is `/health`. To enable the dashboard, set `HBV_ENABLE_DASHBOARD=true` in `/etc/hbv-sentinel/collector.env` and restart. Place the dashboard behind reverse-proxy access control — an IP allowlist, mTLS, a management VLAN, or a separate proxy host that only your operators reach.

When enabled, the page loads an empty shell; paste the API key into the field labeled "API key" and click "Use key." The key stays in the tab's `sessionStorage` and is cleared when the tab closes. The HTML itself contains no secret.

For load balancer / uptime health checks, use `/health` — never the dashboard shell.

### API endpoints (every one requires `X-API-Key` unless marked)

```
POST /api/beacon             # Beacon submission (agents)
GET  /api/agents
GET  /api/beacons/latest
GET  /api/beacons/{agent_id}
GET  /api/alerts
GET  /api/stats
GET  /metrics                # Prometheus
GET  /health                 # (open) liveness probe — the ONLY anonymous route by default
GET  /                       # Dashboard shell — DISABLED unless HBV_ENABLE_DASHBOARD=true
```

`/docs`, `/redoc`, and `/openapi.json` are OFF by default. Set `HBV_ENABLE_DOCS=true` only in a local dev instance.

Example authenticated calls:

```bash
curl -H "X-API-Key: $KEY" https://collector.example.com/api/stats
curl -H "X-API-Key: $KEY" https://collector.example.com/api/agents
curl -H "X-API-Key: $KEY" https://collector.example.com/api/beacons/latest?limit=10
curl -H "X-API-Key: $KEY" "https://collector.example.com/api/beacons/nas-server?limit=10"
curl -H "X-API-Key: $KEY" https://collector.example.com/api/alerts
```

---

## 🚨 Alert system

Alert types: CPU high (>90%), memory high (>90%), disk high (>90%), GPU temp (>85°C), RAID degraded.

Viewing alerts:

```bash
curl -H "X-API-Key: $KEY" https://collector.example.com/api/alerts | jq
curl -H "X-API-Key: $KEY" https://collector.example.com/api/alerts | \
    jq '.alerts[] | select(.agent_id == "NAS")'
```

---

## 🔐 Security considerations

- **Auth is mandatory** on every telemetry endpoint. Do not disable it on any host that can be reached from another machine.
- **Loopback bind is mandatory** for the collector process. TLS termination and LAN exposure happen at the reverse proxy, never at the collector itself.
- **The API key is collector-wide** in the current release: any holder can submit as any `agent_id`. Rotate the key if any host is decommissioned or suspected compromised (see below).
- **The replay guard is in-memory and single-process.** Do not run the collector under a multi-worker uvicorn configuration — it cannot coordinate replay state across workers.
- **Rate limiting is per client IP.** Because the collector sees `127.0.0.1` when a proxy is in front, either terminate the proxy on the same box (where the source IP loses meaning anyway) or add proxy-aware handling before scaling load.

### Key rotation

```bash
# On the collector
NEW_KEY=$(python3 /opt/hbv-sentinel/sentinel-collector.py --generate-key | awk '/Generated/{print $NF}')
echo "$NEW_KEY" | sudo tee /etc/hbv-sentinel/api.key
sudo sed -i "s/^HBV_API_KEY=.*/HBV_API_KEY=$NEW_KEY/" /etc/hbv-sentinel/collector.env
sudo systemctl restart hbv-sentinel-collector

# On every agent
sudo sed -i "s/^HBV_API_KEY=.*/HBV_API_KEY=$NEW_KEY/" /etc/hbv-sentinel/agent.env
sudo systemctl restart hbv-sentinel
```

Windows agents: update the `HBV_API_KEY` machine environment variable and `Restart-ScheduledTask -TaskName "HoneyBadger-Sentinel"`.

---

## 🛠️ Troubleshooting

### Agents not connecting

```bash
# Is the collector process up?
sudo systemctl status hbv-sentinel-collector
sudo journalctl -u hbv-sentinel-collector -n 50

# Can the proxy reach the collector?
curl http://127.0.0.1:8443/health         # on the collector host

# Can the agent reach the proxy?
curl -k -H "X-API-Key: $KEY" https://collector.example.com/health

# Firewall?
sudo ufw status
sudo firewall-cmd --list-all
```

### 401 Unauthorized

Keys don't match. Re-copy the value from `/etc/hbv-sentinel/api.key` on the collector to every agent's `HBV_API_KEY`.

### 503 Service Unavailable on `/api/beacon`

The collector received the beacon but could not persist it. Check `journalctl -u hbv-sentinel-collector -n 100` for the underlying SQLite error (disk full, permissions, corrupt DB). The agent will keep retrying — no beacon is lost.

### "Refusing to send beacon over plaintext HTTP"

The agent's `HBV_COLLECTOR_URL` starts with `http://`. Either switch to `https://` (correct) or, for a lab-only override, set `HBV_ALLOW_INSECURE=true`.

### High beacon queue on an agent

```bash
# Linux
ls -lah /tmp/hbv-sentinel-queue/
journalctl -u hbv-sentinel -n 100
sudo systemctl restart hbv-sentinel

# Windows
Get-ChildItem "$env:TEMP\HBV-Sentinel-Queue"
Restart-ScheduledTask -TaskName "HoneyBadger-Sentinel"
```

### Database maintenance

```bash
# Manual retention prune (keeps last 7 days)
sqlite3 /opt/hbv-sentinel/sentinel.db \
    "DELETE FROM beacons WHERE timestamp < strftime('%s','now','-7 days');"
sqlite3 /opt/hbv-sentinel/sentinel.db "VACUUM;"
```

---

## 🧪 Lab-only override (single host, no TLS)

For local development on a single host where the agent and collector talk over loopback only:

```bash
# On the collector (already loopback by default)
sudo systemctl restart hbv-sentinel-collector

# In the agent's env file
HBV_COLLECTOR_URL=http://127.0.0.1:8443/api/beacon
HBV_ALLOW_INSECURE=true
HBV_API_KEY=<the key from /etc/hbv-sentinel/api.key>
```

This is **not** a production configuration. The agent logs a warning every beacon so it is impossible to leave this mode in place by accident.

---

═══════════════════════════════════════════════════════════
🦡 HoneyBadger Vanguard 2.0 — Sentinel Monitoring System
CyberShield 2026 — Infrastructure Resilience Demonstration
═══════════════════════════════════════════════════════════
