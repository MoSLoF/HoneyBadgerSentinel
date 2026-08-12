#!/usr/bin/env python3
"""
HoneyBadger Sentinel - Central Collector

C2-style infrastructure monitoring collector.
Receives beacons from agents via HTTP/MQTT and stores in time-series database.

Author: HoneyBadger
Version: 1.1.4
CyberShield 2026 - Infrastructure Monitoring
"""

import json
import os
import re
import time
import sqlite3
import logging
import secrets
import asyncio
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, List, Optional, Any
from contextlib import asynccontextmanager
from collections import defaultdict

from fastapi import FastAPI, HTTPException, Request, Depends, Header, status
from fastapi.responses import HTMLResponse, JSONResponse, PlainTextResponse
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field, field_validator
import uvicorn

# ═══════════════════════════════════════════════════════════════════════
# CONFIGURATION (with environment variable support)
# ═══════════════════════════════════════════════════════════════════════

def get_env(key: str, default: str) -> str:
    """Get environment variable with default fallback."""
    return os.environ.get(key, default)

def get_env_int(key: str, default: int) -> int:
    """Get integer environment variable with default fallback."""
    try:
        return int(os.environ.get(key, str(default)))
    except ValueError:
        return default

def get_env_list(key: str, default: List[str]) -> List[str]:
    """Get list environment variable (comma-separated) with default fallback."""
    value = os.environ.get(key)
    if value:
        return [item.strip() for item in value.split(",") if item.strip()]
    return default

# Default API key if not set. This is EPHEMERAL — it changes on every restart,
# which is fine for a quick local test and useless for a real deployment (agents
# would break after a restart). Track whether the operator supplied a real one so
# startup can warn.
DEFAULT_API_KEY = secrets.token_urlsafe(32)
API_KEY_IS_EPHEMERAL = "HBV_API_KEY" not in os.environ

CONFIG = {
    # SECURE-BY-DEFAULT (remediation of the unauthenticated-collector exposure):
    # bind to loopback and require authentication unless the operator explicitly
    # opts out. A remotely reachable collector with auth off was the finding.
    "host": get_env("HBV_HOST", "127.0.0.1"),
    "port": get_env_int("HBV_PORT", 8443),
    "db_path": get_env("HBV_DB_PATH", "/opt/hbv-sentinel/sentinel.db"),
    "retention_days": get_env_int("HBV_RETENTION_DAYS", 30),
    "api_key": get_env("HBV_API_KEY", DEFAULT_API_KEY),
    "api_key_required": get_env("HBV_API_KEY_REQUIRED", "true").lower() == "true",
    # Empty by default: the bundled dashboard is same-origin and needs no CORS.
    # A wildcard "*" is never combined with credentials (see middleware below).
    "allowed_origins": get_env_list("HBV_ALLOWED_ORIGINS", []),
    # Reject beacons whose timestamp is outside this many seconds of now
    # (freshness); also the replay-dedup window.
    "beacon_max_skew": get_env_int("HBV_BEACON_MAX_SKEW", 300),
    # FastAPI's /docs, /redoc, /openapi.json enumerate the API schema without
    # auth. Off by default so a production collector's only anonymous route is
    # /health; flip HBV_ENABLE_DOCS=true for local dev.
    "enable_docs": get_env("HBV_ENABLE_DOCS", "false").lower() == "true",
    # The interactive dashboard shell (GET /) contains no secret but does
    # present a credential-entry UI to any network client — the reviewer's
    # health-only public-route target excludes it. Off by default; enable
    # only from a management origin behind reverse-proxy access control.
    "enable_dashboard": get_env("HBV_ENABLE_DASHBOARD", "false").lower() == "true",
    "rate_limit_requests": get_env_int("HBV_RATE_LIMIT_REQUESTS", 100),
    "rate_limit_window": get_env_int("HBV_RATE_LIMIT_WINDOW", 60),
    "alert_thresholds": {
        "cpu_percent": get_env_int("HBV_ALERT_CPU", 90),
        "memory_percent": get_env_int("HBV_ALERT_MEMORY", 90),
        "disk_percent": get_env_int("HBV_ALERT_DISK", 90),
        "gpu_temp_c": get_env_int("HBV_ALERT_GPU_TEMP", 85)
    }
}

# ═══════════════════════════════════════════════════════════════════════
# LOGGING SETUP
# ═══════════════════════════════════════════════════════════════════════

logging.basicConfig(
    level=getattr(logging, get_env("HBV_LOG_LEVEL", "INFO").upper(), logging.INFO),
    format='%(asctime)s [%(levelname)s] %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S'
)
logger = logging.getLogger('HBV-Collector')

# ═══════════════════════════════════════════════════════════════════════
# PYDANTIC MODELS (Request/Response Validation)
# ═══════════════════════════════════════════════════════════════════════

# Pattern for safe agent IDs (alphanumeric, hyphens, underscores, max 64 chars)
SAFE_AGENT_ID_PATTERN = re.compile(r'^[a-zA-Z0-9_-]{1,64}$')

def sanitize_for_logging(value: str, max_length: int = 64) -> str:
    """Sanitize string for safe logging (prevent log injection)."""
    if not isinstance(value, str):
        value = str(value)
    # Remove control characters and limit length
    sanitized = re.sub(r'[\x00-\x1f\x7f-\x9f]', '', value)
    return sanitized[:max_length]


class RAIDStatus(BaseModel):
    """RAID array status."""
    array: str = Field(..., max_length=32)
    status: str = Field(..., pattern=r'^(healthy|degraded|unknown)$')
    details: Optional[str] = Field(None, max_length=256)


class BeaconRequest(BaseModel):
    """Incoming beacon from agent."""
    agent_id: str = Field(..., min_length=1, max_length=64)
    agent_type: str = Field(..., pattern=r'^(linux|windows|macos|unknown)$')
    timestamp: int = Field(..., ge=0)

    # Optional metrics
    cpu_percent: Optional[float] = Field(None, ge=0, le=100)
    cpu_count: Optional[int] = Field(None, ge=1, le=1024)
    cpu_temp_c: Optional[float] = Field(None, ge=-50, le=200)

    memory_total_mb: Optional[float] = Field(None, ge=0)
    memory_used_mb: Optional[float] = Field(None, ge=0)
    memory_percent: Optional[float] = Field(None, ge=0, le=100)

    disk_total_gb: Optional[float] = Field(None, ge=0)
    disk_used_gb: Optional[float] = Field(None, ge=0)
    disk_percent: Optional[float] = Field(None, ge=0, le=100)

    network_bytes_sent: Optional[int] = Field(None, ge=0)
    network_bytes_recv: Optional[int] = Field(None, ge=0)
    network_adapters: Optional[int] = Field(None, ge=0)
    network_speed_gbps: Optional[float] = Field(None, ge=0)

    uptime_seconds: Optional[int] = Field(None, ge=0)
    load_average: Optional[List[float]] = Field(None, max_length=3)

    gpu_util_percent: Optional[int] = Field(None, ge=0, le=100)
    gpu_mem_util_percent: Optional[int] = Field(None, ge=0, le=100)
    gpu_mem_used_mb: Optional[int] = Field(None, ge=0)
    gpu_mem_total_mb: Optional[int] = Field(None, ge=0)
    gpu_temp_c: Optional[int] = Field(None, ge=-50, le=200)

    raid: Optional[RAIDStatus] = None
    services: Optional[Dict[str, str]] = None

    @field_validator('agent_id')
    @classmethod
    def validate_agent_id(cls, v: str) -> str:
        if not SAFE_AGENT_ID_PATTERN.match(v):
            raise ValueError('agent_id must be alphanumeric with hyphens/underscores only')
        return v

    class Config:
        extra = 'ignore'  # Ignore extra fields for forward compatibility


class BeaconResponse(BaseModel):
    """Response to beacon submission."""
    status: str
    agent_id: str
    alerts: int


class AgentInfo(BaseModel):
    """Agent information."""
    agent_id: str
    agent_type: str
    first_seen: int
    last_seen: int
    total_beacons: int
    time_since_last_beacon: int
    status: str


class AlertInfo(BaseModel):
    """Alert information."""
    agent_id: str
    alert_type: str
    severity: str
    message: str
    timestamp: int
    resolved: bool


class StatsResponse(BaseModel):
    """Collector statistics."""
    agents: Dict[str, int]
    beacons: Dict[str, int]
    alerts: Dict[str, int]


class HealthResponse(BaseModel):
    """Health check response."""
    status: str
    timestamp: int
    version: str = "1.1.4"


# ═══════════════════════════════════════════════════════════════════════
# RATE LIMITING
# ═══════════════════════════════════════════════════════════════════════

class RateLimiter:
    """Simple in-memory rate limiter."""

    def __init__(self, max_requests: int, window_seconds: int):
        self.max_requests = max_requests
        self.window_seconds = window_seconds
        self.requests: Dict[str, List[float]] = defaultdict(list)

    def is_allowed(self, client_id: str) -> bool:
        """Check if request is allowed for client."""
        now = time.time()
        cutoff = now - self.window_seconds

        # Clean old requests
        self.requests[client_id] = [
            t for t in self.requests[client_id] if t > cutoff
        ]

        if len(self.requests[client_id]) >= self.max_requests:
            return False

        self.requests[client_id].append(now)
        return True

    def cleanup(self):
        """Remove stale entries."""
        now = time.time()
        cutoff = now - self.window_seconds * 2

        stale_clients = [
            client for client, times in self.requests.items()
            if not times or max(times) < cutoff
        ]
        for client in stale_clients:
            del self.requests[client]


rate_limiter = RateLimiter(
    max_requests=CONFIG['rate_limit_requests'],
    window_seconds=CONFIG['rate_limit_window']
)

# ═══════════════════════════════════════════════════════════════════════
# REPLAY / FRESHNESS GUARD
# ═══════════════════════════════════════════════════════════════════════

class ReplayGuard:
    """Reject beacons that are stale, post-dated, or replayed.

    Two controls, both bounded by CONFIG['beacon_max_skew']:
      • Freshness — the beacon timestamp must be within ±max_skew of now.
        A captured beacon replayed hours later fails this immediately.
      • De-duplication — a (agent_id, timestamp) pair already accepted inside
        the freshness window is rejected, so an attacker cannot re-post a
        captured-in-flight beacon verbatim while it is still "fresh".

    In-memory only: state is per-process and resets on restart. That is the
    correct scope here — a restart also invalidates the window, so nothing
    older than max_skew could be accepted anyway.
    """

    def __init__(self, max_skew_seconds: int):
        self.max_skew = max_skew_seconds
        self._seen: Dict[str, float] = {}  # "agent_id:timestamp" -> wall time seen

    def _prune(self, wall_now: float) -> None:
        cutoff = wall_now - self.max_skew
        self._seen = {k: v for k, v in self._seen.items() if v > cutoff}

    def check(self, agent_id: str, timestamp: int) -> None:
        now = int(time.time())
        if abs(now - timestamp) > self.max_skew:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Beacon timestamp outside freshness window (±{self.max_skew}s)"
            )

        wall = time.time()
        # Bound memory: prune expired entries once the map grows.
        if len(self._seen) > 4096:
            self._prune(wall)

        key = f"{agent_id}:{timestamp}"
        prev = self._seen.get(key)
        if prev is not None and prev > wall - self.max_skew:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Duplicate or replayed beacon rejected"
            )
        self._seen[key] = wall

    def rollback(self, agent_id: str, timestamp: int) -> None:
        """Undo a check() reservation when downstream persistence fails.

        Without this, a beacon that failed to persist would still poison the
        replay window: the honest retry of the same payload would get 409
        even though nothing was ever stored (review finding S-03).
        """
        self._seen.pop(f"{agent_id}:{timestamp}", None)


replay_guard = ReplayGuard(max_skew_seconds=CONFIG['beacon_max_skew'])

# ═══════════════════════════════════════════════════════════════════════
# DATABASE MANAGEMENT
# ═══════════════════════════════════════════════════════════════════════

class SentinelDatabase:
    def __init__(self, db_path: str):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.init_database()
    
    def get_connection(self):
        """Get database connection"""
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn
    
    def init_database(self):
        """Initialize database schema"""
        with self.get_connection() as conn:
            cursor = conn.cursor()

            # Beacons table (time-series metrics)
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS beacons (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    agent_id TEXT NOT NULL,
                    agent_type TEXT NOT NULL,
                    timestamp INTEGER NOT NULL,
                    received_at INTEGER NOT NULL,
                    metrics TEXT NOT NULL
                )
            ''')
            # Create indices for beacons table
            cursor.execute('''
                CREATE INDEX IF NOT EXISTS idx_beacons_agent_time
                ON beacons (agent_id, timestamp)
            ''')
            cursor.execute('''
                CREATE INDEX IF NOT EXISTS idx_beacons_timestamp
                ON beacons (timestamp)
            ''')

            # Alerts table
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS alerts (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    agent_id TEXT NOT NULL,
                    alert_type TEXT NOT NULL,
                    severity TEXT NOT NULL,
                    message TEXT NOT NULL,
                    timestamp INTEGER NOT NULL,
                    resolved BOOLEAN DEFAULT 0
                )
            ''')
            # Create indices for alerts table
            cursor.execute('''
                CREATE INDEX IF NOT EXISTS idx_alerts_agent
                ON alerts (agent_id)
            ''')
            cursor.execute('''
                CREATE INDEX IF NOT EXISTS idx_alerts_timestamp
                ON alerts (timestamp)
            ''')
            cursor.execute('''
                CREATE INDEX IF NOT EXISTS idx_alerts_resolved
                ON alerts (resolved)
            ''')

            # Agent registry
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS agents (
                    agent_id TEXT PRIMARY KEY,
                    agent_type TEXT NOT NULL,
                    first_seen INTEGER NOT NULL,
                    last_seen INTEGER NOT NULL,
                    total_beacons INTEGER DEFAULT 0
                )
            ''')

            conn.commit()
            logger.info(f"Database initialized at {self.db_path}")
    
    def store_beacon(self, beacon: Dict) -> bool:
        """Store beacon in database."""
        try:
            agent_id = beacon.get('agent_id', 'unknown')
            agent_type = beacon.get('agent_type', 'unknown')
            timestamp = beacon.get('timestamp', int(time.time()))
            received_at = int(time.time())
            metrics_json = json.dumps(beacon)

            # Sanitize for logging
            safe_agent_id = sanitize_for_logging(agent_id)

            with self.get_connection() as conn:
                cursor = conn.cursor()

                # Insert beacon
                cursor.execute('''
                    INSERT INTO beacons (agent_id, agent_type, timestamp, received_at, metrics)
                    VALUES (?, ?, ?, ?, ?)
                ''', (agent_id, agent_type, timestamp, received_at, metrics_json))

                # Update agent registry
                cursor.execute('''
                    INSERT INTO agents (agent_id, agent_type, first_seen, last_seen, total_beacons)
                    VALUES (?, ?, ?, ?, 1)
                    ON CONFLICT(agent_id) DO UPDATE SET
                        last_seen = ?,
                        total_beacons = total_beacons + 1
                ''', (agent_id, agent_type, received_at, received_at, received_at))

                conn.commit()

            logger.info(f"Stored beacon from {safe_agent_id}")
            return True

        except Exception as e:
            logger.error(f"Failed to store beacon: {e}")
            return False
    
    def get_latest_beacons(self, limit: int = 100) -> List[Dict]:
        """Get latest beacons across all agents"""
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute('''
                SELECT agent_id, agent_type, timestamp, received_at, metrics
                FROM beacons
                ORDER BY received_at DESC
                LIMIT ?
            ''', (limit,))
            
            results = []
            for row in cursor.fetchall():
                beacon = json.loads(row['metrics'])
                beacon['received_at'] = row['received_at']
                results.append(beacon)
            
            return results
    
    def get_agent_beacons(self, agent_id: str, limit: int = 100) -> List[Dict]:
        """Get recent beacons for specific agent"""
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute('''
                SELECT timestamp, metrics
                FROM beacons
                WHERE agent_id = ?
                ORDER BY timestamp DESC
                LIMIT ?
            ''', (agent_id, limit))
            
            results = []
            for row in cursor.fetchall():
                beacon = json.loads(row['metrics'])
                results.append(beacon)
            
            return results
    
    def get_active_agents(self) -> List[Dict]:
        """Get list of all active agents"""
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute('''
                SELECT agent_id, agent_type, first_seen, last_seen, total_beacons
                FROM agents
                ORDER BY last_seen DESC
            ''')
            
            results = []
            current_time = int(time.time())
            
            for row in cursor.fetchall():
                agent = dict(row)
                # Calculate time since last beacon
                agent['time_since_last_beacon'] = current_time - row['last_seen']
                agent['status'] = 'online' if agent['time_since_last_beacon'] < 120 else 'offline'
                results.append(agent)
            
            return results
    
    def cleanup_old_data(self, days: int = 30):
        """Remove old data beyond retention period"""
        cutoff_time = int(time.time()) - (days * 86400)
        
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute('DELETE FROM beacons WHERE timestamp < ?', (cutoff_time,))
            deleted = cursor.rowcount
            conn.commit()
            
            if deleted > 0:
                logger.info(f"Cleaned up {deleted} old beacons")

# ═══════════════════════════════════════════════════════════════════════
# ALERT ENGINE
# ═══════════════════════════════════════════════════════════════════════

class AlertEngine:
    def __init__(self, db: SentinelDatabase):
        self.db = db
        self.thresholds = CONFIG['alert_thresholds']
    
    def check_beacon(self, beacon: Dict) -> List[Dict]:
        """Check beacon for alert conditions"""
        alerts = []
        agent_id = beacon.get('agent_id', 'unknown')
        
        # CPU threshold
        cpu = beacon.get('cpu_percent')
        if cpu and cpu > self.thresholds['cpu_percent']:
            alerts.append({
                'agent_id': agent_id,
                'alert_type': 'cpu_high',
                'severity': 'warning',
                'message': f"CPU usage high: {cpu}%"
            })
        
        # Memory threshold
        memory = beacon.get('memory_percent')
        if memory and memory > self.thresholds['memory_percent']:
            alerts.append({
                'agent_id': agent_id,
                'alert_type': 'memory_high',
                'severity': 'warning',
                'message': f"Memory usage high: {memory}%"
            })
        
        # Disk threshold
        disk = beacon.get('disk_percent')
        if disk and disk > self.thresholds['disk_percent']:
            alerts.append({
                'agent_id': agent_id,
                'alert_type': 'disk_high',
                'severity': 'critical',
                'message': f"Disk usage high: {disk}%"
            })
        
        # GPU temperature
        gpu_temp = beacon.get('gpu_temp_c')
        if gpu_temp and gpu_temp > self.thresholds['gpu_temp_c']:
            alerts.append({
                'agent_id': agent_id,
                'alert_type': 'gpu_temp_high',
                'severity': 'critical',
                'message': f"GPU temperature high: {gpu_temp}°C"
            })
        
        # RAID degraded
        raid = beacon.get('raid')
        if raid and raid.get('status') == 'degraded':
            alerts.append({
                'agent_id': agent_id,
                'alert_type': 'raid_degraded',
                'severity': 'critical',
                'message': f"RAID array degraded: {raid.get('details')}"
            })
        
        return alerts
    
    def store_alerts(self, alerts: List[Dict]):
        """Store alerts in database"""
        if not alerts:
            return
        
        try:
            with self.db.get_connection() as conn:
                cursor = conn.cursor()
                timestamp = int(time.time())
                
                for alert in alerts:
                    cursor.execute('''
                        INSERT INTO alerts (agent_id, alert_type, severity, message, timestamp)
                        VALUES (?, ?, ?, ?, ?)
                    ''', (alert['agent_id'], alert['alert_type'], 
                         alert['severity'], alert['message'], timestamp))
                
                conn.commit()
                logger.warning(f"Generated {len(alerts)} alerts")
                
        except Exception as e:
            logger.error(f"Failed to store alerts: {e}")

# ═══════════════════════════════════════════════════════════════════════
# FASTAPI APPLICATION
# ═══════════════════════════════════════════════════════════════════════

# Initialize database and alert engine
db = SentinelDatabase(CONFIG['db_path'])
alert_engine = AlertEngine(db)

# Shutdown event for graceful cleanup
shutdown_event = asyncio.Event()


async def cleanup_task():
    """Periodic cleanup task for rate limiter and old data."""
    while not shutdown_event.is_set():
        try:
            await asyncio.sleep(300)  # Every 5 minutes
            rate_limiter.cleanup()
            db.cleanup_old_data(CONFIG['retention_days'])
        except asyncio.CancelledError:
            break
        except Exception as e:
            logger.error(f"Cleanup task error: {e}")


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup and shutdown events."""
    logger.info("🦡 HoneyBadger Sentinel Collector v1.1.4 starting...")
    logger.info(f"Database: {CONFIG['db_path']}")
    logger.info(f"Listening on {CONFIG['host']}:{CONFIG['port']}")

    host = CONFIG['host']
    if host not in ("127.0.0.1", "::1", "localhost"):
        logger.warning(
            "⚠ Binding to a NON-LOOPBACK address (%s): the collector is reachable "
            "from the network. Ensure a firewall / TLS reverse proxy fronts it and "
            "authentication is enabled.", host)
    if CONFIG['api_key_required']:
        logger.info("API key authentication: ENABLED")
        if API_KEY_IS_EPHEMERAL:
            logger.warning(
                "⚠ HBV_API_KEY is not set — using an EPHEMERAL key that changes on "
                "every restart. Set HBV_API_KEY for any real deployment or agents "
                "will break after a restart.")
    else:
        logger.warning(
            "⚠ API key authentication is DISABLED — every telemetry endpoint is "
            "unauthenticated. Set HBV_API_KEY_REQUIRED=true (the default) to enable.")

    # Start background cleanup task
    cleanup = asyncio.create_task(cleanup_task())

    yield

    # Graceful shutdown
    logger.info("Shutting down...")
    shutdown_event.set()
    cleanup.cancel()
    try:
        await cleanup
    except asyncio.CancelledError:
        pass


app = FastAPI(
    title="HoneyBadger Sentinel Collector",
    description="C2-style infrastructure monitoring collector",
    version="1.1.4",
    lifespan=lifespan,
    # Docs/schema routes are OFF by default (review finding S-06). Public
    # attack surface stays limited to /health and the empty dashboard shell.
    docs_url="/docs" if CONFIG['enable_docs'] else None,
    redoc_url="/redoc" if CONFIG['enable_docs'] else None,
    openapi_url="/openapi.json" if CONFIG['enable_docs'] else None,
)

# CORS: NEVER combine wildcard origins with credentials. That pairing is both a
# spec violation (browsers reject it) and, if it were honored, a cross-origin
# credential leak. Default origins are empty (same-origin dashboard needs none);
# if an operator opts into "*", credentials are force-disabled with a warning.
_cors_origins = CONFIG['allowed_origins']
_cors_wildcard = "*" in _cors_origins
if _cors_wildcard:
    logger.warning(
        "CORS wildcard '*' origins configured; disabling credentialed CORS to "
        "avoid a spec-invalid, credential-leaking configuration."
    )
app.add_middleware(
    CORSMiddleware,
    allow_origins=_cors_origins,
    allow_credentials=not _cors_wildcard,
    allow_methods=["GET", "POST"],
    allow_headers=["*"],
)


# ═══════════════════════════════════════════════════════════════════════
# AUTHENTICATION & RATE LIMITING DEPENDENCIES
# ═══════════════════════════════════════════════════════════════════════

async def verify_api_key(
    x_api_key: Optional[str] = Header(None, alias="X-API-Key")
) -> bool:
    """Verify API key if authentication is required."""
    if not CONFIG['api_key_required']:
        return True

    if not x_api_key:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing X-API-Key header"
        )

    if not secrets.compare_digest(x_api_key, CONFIG['api_key']):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid API key"
        )

    return True


async def check_rate_limit(request: Request) -> bool:
    """Check rate limit for client."""
    client_ip = request.client.host if request.client else "unknown"

    if not rate_limiter.is_allowed(client_ip):
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Rate limit exceeded. Please slow down."
        )

    return True

# ═══════════════════════════════════════════════════════════════════════
# API ENDPOINTS
# ═══════════════════════════════════════════════════════════════════════

@app.post("/api/beacon", response_model=BeaconResponse)
async def receive_beacon(
    beacon: BeaconRequest,
    _auth: bool = Depends(verify_api_key),
    _rate: bool = Depends(check_rate_limit)
):
    """Receive beacon from agent.

    Persistence is authoritative: if store_beacon returns False, the handler
    returns 503 (retryable) and rolls back the replay reservation so an honest
    retry of the same payload can succeed once storage recovers. See review
    finding S-03 — the earlier version acknowledged "success" even on a failed
    write, which permanently lost telemetry during disk/permission trouble.
    """
    reserved = False
    try:
        # Freshness + replay control: reject stale/post-dated timestamps and
        # verbatim replays before doing any work or touching the database.
        replay_guard.check(beacon.agent_id, beacon.timestamp)
        reserved = True

        # Convert Pydantic model to dict for storage
        beacon_dict = beacon.model_dump(exclude_none=True)

        safe_agent_id = sanitize_for_logging(beacon.agent_id)

        # Store beacon — DO NOT log "received" until persistence commits.
        if not db.store_beacon(beacon_dict):
            replay_guard.rollback(beacon.agent_id, beacon.timestamp)
            reserved = False
            logger.error(f"Beacon from {safe_agent_id} was NOT persisted")
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="Beacon storage unavailable; retry the beacon"
            )

        logger.info(f"Beacon persisted from {safe_agent_id}")

        # Check for alerts (alert-store failures are logged but do not fail
        # the request — the beacon itself is already durable).
        alerts = alert_engine.check_beacon(beacon_dict)
        if alerts:
            alert_engine.store_alerts(alerts)

        return BeaconResponse(
            status="success",
            agent_id=beacon.agent_id,
            alerts=len(alerts)
        )

    except HTTPException:
        raise
    except Exception as e:
        # Any unexpected exception in the pipeline: roll back the reservation
        # so the honest retry is not falsely rejected as a duplicate.
        if reserved:
            replay_guard.rollback(beacon.agent_id, beacon.timestamp)
        logger.error(f"Error processing beacon: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Internal server error"
        )

@app.get("/api/agents")
async def get_agents(
    _auth: bool = Depends(verify_api_key),
    _rate: bool = Depends(check_rate_limit)
):
    """Get list of all agents."""
    agents = db.get_active_agents()
    return JSONResponse({"agents": agents})


@app.get("/api/beacons/latest")
async def get_latest_beacons(
    limit: int = 100,
    _auth: bool = Depends(verify_api_key),
    _rate: bool = Depends(check_rate_limit)
):
    """Get latest beacons across all agents."""
    # Enforce reasonable limit
    limit = min(max(1, limit), 1000)
    beacons = db.get_latest_beacons(limit=limit)
    return JSONResponse({"beacons": beacons})


@app.get("/api/beacons/{agent_id}")
async def get_agent_beacons(
    agent_id: str,
    limit: int = 100,
    _auth: bool = Depends(verify_api_key),
    _rate: bool = Depends(check_rate_limit)
):
    """Get beacons for specific agent."""
    # Validate agent_id format
    if not SAFE_AGENT_ID_PATTERN.match(agent_id):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid agent_id format"
        )

    limit = min(max(1, limit), 1000)
    beacons = db.get_agent_beacons(agent_id, limit=limit)
    return JSONResponse({"agent_id": agent_id, "beacons": beacons})


@app.get("/api/alerts")
async def get_alerts(
    resolved: bool = False,
    limit: int = 100,
    _auth: bool = Depends(verify_api_key),
    _rate: bool = Depends(check_rate_limit)
):
    """Get recent alerts."""
    limit = min(max(1, limit), 1000)

    with db.get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute('''
            SELECT agent_id, alert_type, severity, message, timestamp, resolved
            FROM alerts
            WHERE resolved = ?
            ORDER BY timestamp DESC
            LIMIT ?
        ''', (1 if resolved else 0, limit))

        alerts = [dict(row) for row in cursor.fetchall()]

    return JSONResponse({"alerts": alerts})


@app.get("/api/stats", response_model=StatsResponse)
async def get_stats(
    _auth: bool = Depends(verify_api_key),
    _rate: bool = Depends(check_rate_limit)
):
    """Get collector statistics."""
    agents = db.get_active_agents()

    online_count = sum(1 for a in agents if a['status'] == 'online')
    offline_count = sum(1 for a in agents if a['status'] == 'offline')

    with db.get_connection() as conn:
        cursor = conn.cursor()

        # Total beacons
        cursor.execute('SELECT COUNT(*) as count FROM beacons')
        total_beacons = cursor.fetchone()['count']

        # Beacons last hour
        hour_ago = int(time.time()) - 3600
        cursor.execute('SELECT COUNT(*) as count FROM beacons WHERE timestamp > ?', (hour_ago,))
        beacons_last_hour = cursor.fetchone()['count']

        # Unresolved alerts
        cursor.execute('SELECT COUNT(*) as count FROM alerts WHERE resolved = 0')
        unresolved_alerts = cursor.fetchone()['count']

    return StatsResponse(
        agents={
            "total": len(agents),
            "online": online_count,
            "offline": offline_count
        },
        beacons={
            "total": total_beacons,
            "last_hour": beacons_last_hour
        },
        alerts={
            "unresolved": unresolved_alerts
        }
    )

def _register_dashboard(_app: FastAPI) -> None:
    """Register GET / only when HBV_ENABLE_DASHBOARD=true (review R-02).

    When disabled, no route is defined at /, so GET / returns 404 and the
    route inventory is truly `{"/health"}` — matching the reviewer's
    health-only public-route criterion. When enabled, the same interactive
    shell as before is served; operators should place this behind reverse-
    proxy access control (IP allowlist, mTLS, or a management VLAN).
    """
    @_app.get("/", response_class=HTMLResponse)
    async def root():
        return _DASHBOARD_HTML


_DASHBOARD_HTML = """<!DOCTYPE html>
<html>
<head>
    <title>HoneyBadger Sentinel</title>
    <style>
        body { background: #1a1a1a; color: #00ff00;
               font-family: 'Courier New', monospace; padding: 20px; }
        h1 { color: #ff0000; }
        .container { max-width: 1200px; margin: 0 auto; }
        .stats { display: grid; grid-template-columns: repeat(3, 1fr); gap: 20px; margin: 20px 0; }
        .stat-box { background: #2a2a2a; padding: 20px; border: 2px solid #00ff00; }
        .stat-value { font-size: 2em; font-weight: bold; }
        pre { background: #0a0a0a; padding: 15px; border: 1px solid #333; overflow-x: auto; }
        .authbar { background: #2a2a2a; padding: 12px; border: 1px solid #333; margin: 12px 0; }
        .authbar input { background: #0a0a0a; color: #00ff00; border: 1px solid #00ff00;
                         font-family: inherit; padding: 6px; width: 320px; }
        .authbar button { background: #00ff00; color: #0a0a0a; border: none;
                          font-family: inherit; padding: 6px 12px; cursor: pointer; }
        #auth-status { color: #ffb020; margin-left: 12px; }
    </style>
</head>
<body>
    <div class="container">
        <h1>🦡 HoneyBadger Sentinel Collector</h1>
        <p>C2-Style Infrastructure Monitoring</p>
        <div class="authbar">
            <label for="api-key">API key:</label>
            <input type="password" id="api-key" placeholder="paste X-API-Key value" autocomplete="off">
            <button id="api-key-save" type="button">Use key</button>
            <span id="auth-status">no key set</span>
        </div>
        <div class="stats">
            <div class="stat-box"><div>Agents Online</div><div class="stat-value" id="agents-online">-</div></div>
            <div class="stat-box"><div>Beacons (Last Hour)</div><div class="stat-value" id="beacons-hour">-</div></div>
            <div class="stat-box"><div>Active Alerts</div><div class="stat-value" id="alerts">-</div></div>
        </div>
        <h2>API Endpoints</h2>
        <pre>
GET  /api/stats              - Collector statistics        (auth required)
GET  /api/agents             - List all agents             (auth required)
GET  /api/beacons/latest     - Latest beacons              (auth required)
GET  /api/beacons/{agent_id} - Beacons for one agent       (auth required)
GET  /api/alerts             - Recent alerts               (auth required)
GET  /metrics                - Prometheus metrics          (auth required)
POST /api/beacon             - Receive beacon (agent)      (auth required)
GET  /health                 - Liveness probe              (open)
        </pre>
    </div>
    <script>
        function getKey() { return sessionStorage.getItem('hbv_api_key') || ''; }
        function setStatus(msg) { document.getElementById('auth-status').textContent = msg; }
        document.getElementById('api-key-save').addEventListener('click', function () {
            const v = document.getElementById('api-key').value.trim();
            if (v) {
                sessionStorage.setItem('hbv_api_key', v);
                document.getElementById('api-key').value = '';
                setStatus('key set for this tab');
                updateStats();
            } else {
                sessionStorage.removeItem('hbv_api_key');
                setStatus('no key set');
            }
        });
        async function updateStats() {
            const key = getKey();
            const headers = key ? { 'X-API-Key': key } : {};
            try {
                const response = await fetch('/api/stats', { headers });
                if (response.status === 401) { setStatus('unauthorized — paste a valid API key'); return; }
                if (!response.ok) { setStatus('error: HTTP ' + response.status); return; }
                const data = await response.json();
                document.getElementById('agents-online').textContent = data.agents.online;
                document.getElementById('beacons-hour').textContent = data.beacons.last_hour;
                document.getElementById('alerts').textContent = data.alerts.unresolved;
                setStatus(key ? 'connected' : 'connected (auth disabled)');
            } catch (e) {
                setStatus('fetch failed — is the collector reachable?');
                console.error('Failed to fetch stats:', e);
            }
        }
        if (getKey()) setStatus('key set for this tab');
        updateStats();
        setInterval(updateStats, 5000);
    </script>
</body>
</html>
"""


if CONFIG['enable_dashboard']:
    _register_dashboard(app)


@app.get("/health", response_model=HealthResponse)
async def health_check():
    """Health check endpoint (no rate limiting)."""
    return HealthResponse(
        status="healthy",
        timestamp=int(time.time()),
        version="1.1.4"
    )


@app.get("/metrics")
async def prometheus_metrics(_auth: bool = Depends(verify_api_key)):
    """Prometheus-compatible metrics endpoint."""
    agents = db.get_active_agents()
    online_count = sum(1 for a in agents if a['status'] == 'online')
    offline_count = sum(1 for a in agents if a['status'] == 'offline')

    with db.get_connection() as conn:
        cursor = conn.cursor()

        cursor.execute('SELECT COUNT(*) as count FROM beacons')
        total_beacons = cursor.fetchone()['count']

        hour_ago = int(time.time()) - 3600
        cursor.execute('SELECT COUNT(*) as count FROM beacons WHERE timestamp > ?', (hour_ago,))
        beacons_last_hour = cursor.fetchone()['count']

        cursor.execute('SELECT COUNT(*) as count FROM alerts WHERE resolved = 0')
        unresolved_alerts = cursor.fetchone()['count']

        cursor.execute('SELECT COUNT(*) as count FROM alerts')
        total_alerts = cursor.fetchone()['count']

    # Format as Prometheus text exposition format
    metrics_text = f"""# HELP hbv_agents_total Total number of registered agents
# TYPE hbv_agents_total gauge
hbv_agents_total {len(agents)}

# HELP hbv_agents_online Number of online agents
# TYPE hbv_agents_online gauge
hbv_agents_online {online_count}

# HELP hbv_agents_offline Number of offline agents
# TYPE hbv_agents_offline gauge
hbv_agents_offline {offline_count}

# HELP hbv_beacons_total Total number of beacons received
# TYPE hbv_beacons_total counter
hbv_beacons_total {total_beacons}

# HELP hbv_beacons_last_hour Beacons received in the last hour
# TYPE hbv_beacons_last_hour gauge
hbv_beacons_last_hour {beacons_last_hour}

# HELP hbv_alerts_total Total number of alerts generated
# TYPE hbv_alerts_total counter
hbv_alerts_total {total_alerts}

# HELP hbv_alerts_unresolved Number of unresolved alerts
# TYPE hbv_alerts_unresolved gauge
hbv_alerts_unresolved {unresolved_alerts}

# HELP hbv_rate_limiter_clients Number of clients tracked by rate limiter
# TYPE hbv_rate_limiter_clients gauge
hbv_rate_limiter_clients {len(rate_limiter.requests)}

# HELP hbv_info Collector version information
# TYPE hbv_info gauge
hbv_info{{version="1.1.4"}} 1
"""

    return PlainTextResponse(
        content=metrics_text,
        media_type="text/plain; version=0.0.4; charset=utf-8"
    )


# ═══════════════════════════════════════════════════════════════════════
# MAIN
# ═══════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description='HoneyBadger Sentinel Collector v1.1.4')
    parser.add_argument('--host', default=CONFIG['host'], help='Host to bind to')
    parser.add_argument('--port', type=int, default=CONFIG['port'], help='Port to bind to')
    parser.add_argument('--generate-key', action='store_true', help='Generate a new API key and exit')

    args = parser.parse_args()

    if args.generate_key:
        new_key = secrets.token_urlsafe(32)
        print(f"Generated API Key: {new_key}")
        print(f"\nTo use this key:")
        print(f"  export HBV_API_KEY='{new_key}'")
        print(f"  export HBV_API_KEY_REQUIRED='true'")
    else:
        uvicorn.run(
            app,
            host=args.host,
            port=args.port,
            log_level=get_env("HBV_LOG_LEVEL", "info").lower(),
            timeout_keep_alive=30,
            access_log=True
        )
