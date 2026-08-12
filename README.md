# 🦡 HoneyBadger Sentinel

**C2-Style Infrastructure Monitoring System for CyberShield 2026**

A distributed monitoring system with Command & Control beacon architecture designed to showcase advanced red team infrastructure techniques in a legitimate blue team application.

**Version:** 1.1.2 (hardened production baseline)

---

## 🎯 Features

- **C2-Style Beacon Architecture** — Agents beacon metrics like offensive security implants
- **Offline Resilience** — Agents queue beacons when the collector is unavailable
- **Multi-Platform** — Windows (PowerShell) and Linux (Python) agents
- **Real-Time Monitoring** — 30-second beacon intervals with time-series storage
- **Alert Engine** — Automated threshold-based alerting
- **Web Dashboard** — Real-time visualization; ships no secret client-side
- **Prometheus Integration** — Authenticated `/metrics` endpoint

### v1.1.x security posture (default)

- **Loopback bind by default** (`HBV_HOST=127.0.0.1`)
- **API-key authentication REQUIRED by default** on every telemetry endpoint
- **HTTPS-first agent transport** — both Linux and Windows agents refuse `http://` unless `HBV_ALLOW_INSECURE=true` is set
- **CORS empty by default**; wildcard origins auto-force `allow_credentials=false`
- **Beacon freshness + replay dedup** (±`HBV_BEACON_MAX_SKEW` seconds)
- **Failed persistence returns 503**, not a phantom "success"
- **FastAPI docs/schema routes closed** unless `HBV_ENABLE_DOCS=true`

---

## 📦 Components

```
honeybadger-sentinel/
├── sentinel-collector.py            # Central FastAPI collector
├── sentinel-agent-linux.py          # Python agent for Linux
├── Sentinel-Agent-Windows.ps1       # PowerShell agent for Windows
├── install-collector.sh             # Collector installation (hardened defaults)
├── install-agent-linux.sh           # Linux agent installation
├── requirements.txt                 # Python dependencies
├── .env.example                     # Environment configuration template
├── config/
│   └── logrotate.conf               # Log rotation configuration
├── scripts/
│   └── backup-db.sh                 # Database backup script
├── tests/
│   └── test_collector.py            # Real-app integration tests
├── .github/workflows/
│   └── ci.yml                       # SHA-pinned GitHub Actions CI
├── SECURITY.md                      # Security posture and upgrade notes
├── DEPLOYMENT-GUIDE.md              # Complete deployment guide
├── INSTALLATION-CHECKLIST.md        # Step-by-step deployment checklist
└── README.md                        # This file
```

---

## 🚀 Deployment overview (production path)

The **only** supported production topology is a TLS-terminating reverse proxy in front of a loopback-bound collector, with a stable API key shared to every agent.

```
                           ┌────────────────────────────────┐
                           │  Reverse proxy (nginx/caddy)   │
                           │  https://collector.example:443 │
                           │  ─ terminates TLS              │
                           │  ─ forwards → 127.0.0.1:8443   │
                           └───────────────┬────────────────┘
                                           │ loopback
                           ┌───────────────▼────────────────┐
                           │  sentinel-collector (127.0.0.1)│
                           │  ─ API key required            │
                           │  ─ SQLite + alert engine       │
                           └────────────────────────────────┘
                                           ▲
                             HTTPS + X-API-Key
                                           │
              ┌────────────────┬───────────┴────────────┬────────────────┐
              │                │                        │                │
          ┌───▼───┐        ┌───▼───┐                ┌───▼───┐        ┌───▼───┐
          │Linux  │        │Linux  │                │Windows│        │Windows│
          │ agent │        │ agent │                │ agent │        │ agent │
          └───────┘        └───────┘                └───────┘        └───────┘
```

For a laboratory-only bring-up on a single host, agent and collector may talk over loopback HTTP with `HBV_ALLOW_INSECURE=true`. See DEPLOYMENT-GUIDE.md.

---

## ⚡ Quick start (production path)

**On the collector host:**

```bash
sudo ./install-collector.sh
# Note the generated key location: /etc/hbv-sentinel/api.key
sudo cat /etc/hbv-sentinel/api.key           # copy this to each agent

# Loopback health check (no auth):
curl http://127.0.0.1:8443/health

# Authenticated stats check:
curl -H "X-API-Key: $(sudo cat /etc/hbv-sentinel/api.key)" \
     http://127.0.0.1:8443/api/stats
```

Then stand up your TLS reverse proxy on the LAN interface, terminating TLS and forwarding to `127.0.0.1:8443`. Keep the collector on loopback — the proxy is what listens externally.

**On each Linux agent:**

```bash
sudo ./install-agent-linux.sh
sudo tee -a /etc/hbv-sentinel/agent.env <<'EOF'
HBV_COLLECTOR_URL=https://collector.example.com:443/api/beacon
HBV_API_KEY=<paste the key from /etc/hbv-sentinel/api.key on the collector>
# If the proxy uses a private CA:
# HBV_TLS_CA_BUNDLE=/etc/hbv-sentinel/ca.pem
EOF
sudo systemctl restart hbv-sentinel
```

**On each Windows agent (PowerShell as Administrator):**

```powershell
Copy-Item Sentinel-Agent-Windows.ps1 C:\HBV\
[Environment]::SetEnvironmentVariable(
    "HBV_COLLECTOR_URL",
    "https://collector.example.com:443/api/beacon",
    "Machine")
[Environment]::SetEnvironmentVariable(
    "HBV_API_KEY",
    "<paste the key here>",
    "Machine")
cd C:\HBV
.\Sentinel-Agent-Windows.ps1 -Install
Start-ScheduledTask -TaskName "HoneyBadger-Sentinel"
```

The Windows agent, like the Linux agent, will refuse to POST beacons over plaintext HTTP unless `HBV_ALLOW_INSECURE=true` is set at the machine environment.

---

## 🔧 Configuration reference

All configuration is via environment variables. Set them in:
- `/etc/hbv-sentinel/collector.env` (collector, loaded by systemd)
- `/etc/hbv-sentinel/agent.env` (Linux agents, loaded by systemd)
- Machine environment (Windows agents)

### Collector — key variables

| Variable | Default | Description |
|----------|---------|-------------|
| `HBV_HOST` | `127.0.0.1` | Bind address. Keep loopback and use a TLS proxy for LAN exposure. |
| `HBV_PORT` | `8443` | Collector listen port. |
| `HBV_API_KEY` | (ephemeral) | Shared API key. **Set to a stable value in production.** |
| `HBV_API_KEY_REQUIRED` | `true` | Authentication enforcement. Do not disable on a reachable host. |
| `HBV_ALLOWED_ORIGINS` | (empty) | CORS origin allowlist. Empty is the correct default. |
| `HBV_BEACON_MAX_SKEW` | `300` | Freshness window in seconds (also replay-dedup window). |
| `HBV_ENABLE_DOCS` | `false` | Set to `true` only in dev to enable `/docs`, `/redoc`, `/openapi.json`. |
| `HBV_ENABLE_DASHBOARD` | `false` | Set to `true` to expose the interactive dashboard at `GET /`. Place it behind reverse-proxy access control. |
| `HBV_RETENTION_DAYS` | `30` | Beacon retention. |
| `HBV_LOG_LEVEL` | `INFO` | Logging level. |

### Agent — key variables

| Variable | Default | Description |
|----------|---------|-------------|
| `HBV_COLLECTOR_URL` | `https://<COLLECTOR_HOST>:8443/api/beacon` | Must start with `https://` unless `HBV_ALLOW_INSECURE=true`. |
| `HBV_API_KEY` | *(unset)* | Must match the collector's `HBV_API_KEY`. |
| `HBV_TLS_CA_BUNDLE` | *(unset)* | Path to a private-CA bundle for a self-signed collector. Set to the literal string `false` to disable TLS verification (lab only, logs a warning). |
| `HBV_ALLOW_INSECURE` | `false` | Set to `true` to allow plaintext `http://`. **Lab / loopback only.** |
| `HBV_BEACON_INTERVAL` | `30` | Beacon interval in seconds. |
| `HBV_LOG_LEVEL` | `INFO` | Logging level. |

See `.env.example` for the full list.

### Alert thresholds

```bash
HBV_ALERT_CPU=90        # CPU usage %
HBV_ALERT_MEMORY=90     # Memory usage %
HBV_ALERT_DISK=90       # Disk usage %
HBV_ALERT_GPU_TEMP=85   # GPU temperature °C
```

---

## 📡 Metrics collected

**All platforms:** CPU, memory, disk, network stats, uptime.
**Windows-specific:** GPU utilization/temperature (NVIDIA), service status (Ollama, Docker).
**Linux-specific:** CPU temperature, RAID array status, load average.

---

## 🚨 Alert types

CPU high (>90%), memory high (>90%), disk high (>90%), GPU temperature (>85°C), RAID degraded.

---

## 📊 API endpoints

Every endpoint below requires `X-API-Key` unless marked "(open)".

```
POST /api/beacon             # Beacon submission (agents)
GET  /api/agents             # List all agents
GET  /api/beacons/latest     # Latest beacons across all agents
GET  /api/beacons/{agent_id} # Beacons for one agent
GET  /api/alerts             # Recent alerts
GET  /api/stats              # Aggregate statistics
GET  /metrics                # Prometheus metrics
GET  /health                 # Liveness probe (open)
GET  /                       # Dashboard shell (DISABLED by default; opt-in)
```

`/docs`, `/redoc`, and `/openapi.json` are DISABLED by default. Set `HBV_ENABLE_DOCS=true` to enable them (dev only).
`GET /` (interactive dashboard) is also DISABLED by default; set `HBV_ENABLE_DASHBOARD=true` and place it behind reverse-proxy access control (IP allowlist, mTLS, or a management VLAN).

Example authenticated call:

```bash
curl -H "X-API-Key: $HBV_API_KEY" https://collector.example.com/api/stats
```

### Prometheus integration

```yaml
scrape_configs:
  - job_name: 'hbv-sentinel'
    static_configs:
      - targets: ['collector.example.com:443']
    scheme: https
    metrics_path: /metrics
    authorization:
      type: 'X-API-Key'
      credentials: '<the shared key>'
```

---

## 🛠️ Management commands

### Collector

```bash
systemctl status hbv-sentinel-collector
systemctl restart hbv-sentinel-collector
journalctl -u hbv-sentinel-collector -f
```

### Linux agents

```bash
systemctl status hbv-sentinel
systemctl restart hbv-sentinel
journalctl -u hbv-sentinel -f
```

### Windows agents

```powershell
Get-ScheduledTask -TaskName "HoneyBadger-Sentinel"
Get-Content "$env:TEMP\HBV-Sentinel.log" -Tail 50
Stop-ScheduledTask  -TaskName "HoneyBadger-Sentinel"
Start-ScheduledTask -TaskName "HoneyBadger-Sentinel"
```

---

## 🔐 Security notes

See `SECURITY.md` for the full posture, upgrade impact, and known limitations. Highlights:

- The API key is **collector-wide** in the current release: any holder can submit as any `agent_id`. Per-agent identity is a planned change; until then, treat the key as a shared secret and rotate it if any host is decommissioned or suspected compromised.
- The replay/freshness guard is **in-memory and per-process**. Do not run the collector under a multi-worker uvicorn configuration — the current guard cannot coordinate across workers.
- Rate limiting is per client IP. Behind a reverse proxy, either terminate the proxy on the same box (so `request.client.host` is meaningful) or add `X-Forwarded-For` handling before increasing traffic volume.
- The dashboard shell at `GET /` is DISABLED by default (`HBV_ENABLE_DASHBOARD=false`). When explicitly enabled, it ships no secret in HTML — the operator pastes the key at view time and it lives only in the tab's `sessionStorage`. Even so, an interactive credential-entry surface belongs behind reverse-proxy access control; the recommended posture is to keep it off in production and open it only from a management origin (IP allowlist, mTLS, or a management VLAN).

### Key rotation

1. Generate a new key: `python3 /opt/hbv-sentinel/sentinel-collector.py --generate-key`
2. Write the new value into `/etc/hbv-sentinel/api.key` and `HBV_API_KEY` in `collector.env`.
3. Distribute the new value to every agent's `HBV_API_KEY`.
4. Restart the collector, then restart each agent.

---

## 🔄 Backup & maintenance

### Database backup

```bash
sudo /opt/hbv-sentinel/scripts/backup-db.sh
echo "0 2 * * * /opt/hbv-sentinel/scripts/backup-db.sh --cron" | sudo crontab -
```

Backups stored in `/opt/hbv-sentinel/backups/` (7-day retention).

### Log rotation

```bash
sudo cp config/logrotate.conf /etc/logrotate.d/hbv-sentinel
```

### Running tests

```bash
pip install fastapi 'pydantic>=2.5' httpx pytest
pytest tests/ -v
```

CI runs the same suite on Python 3.10, 3.11, and 3.12 with SHA-pinned GitHub Actions (see `.github/workflows/ci.yml`).

---

## 🛠️ Troubleshooting

### Agents not connecting

```bash
# Collector healthy?
sudo systemctl status hbv-sentinel-collector
curl http://127.0.0.1:8443/health

# From the agent host:
curl -H "X-API-Key: $HBV_API_KEY" https://collector.example.com/api/stats
```

If the agent logs "Refusing to send beacon over plaintext HTTP," the `HBV_COLLECTOR_URL` is `http://…`. Either switch to `https://` (correct) or set `HBV_ALLOW_INSECURE=true` (lab only).

### No beacons received

```bash
# Agent service state
systemctl status hbv-sentinel                    # Linux
Get-ScheduledTask HoneyBadger-Sentinel           # Windows

# Agent logs
journalctl -u hbv-sentinel -f                    # Linux
Get-Content $env:TEMP\HBV-Sentinel.log -Tail 50  # Windows
```

### 401 Unauthorized from every call

The collector's `HBV_API_KEY` and the caller's `X-API-Key` header do not match. Re-copy the value from `/etc/hbv-sentinel/api.key` on the collector.

### 503 Service Unavailable on `/api/beacon`

The collector received the beacon but could not persist it (disk full, permissions, corrupt SQLite). Check `journalctl -u hbv-sentinel-collector -n 50`. The agent will retry — no beacon is lost.

---

## 🦡 About

Created for **CyberShield 2026** demonstration by HoneyBadger. Demonstrates advanced infrastructure monitoring using offensive security design patterns in a legitimate defensive application.

═══════════════════════════════════════════════════════════
🦡 HoneyBadger Vanguard 2.0 — Infrastructure Monitoring
CyberShield 2026 — 198 Days Remaining
═══════════════════════════════════════════════════════════
