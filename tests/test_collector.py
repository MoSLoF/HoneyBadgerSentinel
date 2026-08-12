#!/usr/bin/env python3
"""
HoneyBadger Sentinel — Collector Tests

These tests exercise the REAL FastAPI application in sentinel-collector.py.
They do NOT re-implement production logic (a previous version of this file
tested private copies of the regex/rate-limiter/alert code, which meant the
tests could stay green even if the real endpoints regressed).

Rules of the road:
  - Import via importlib because the module filename contains a hyphen.
  - Configure via HBV_* environment variables BEFORE import; the collector
    reads them at module-load time.
  - Each test creates a FRESH FastAPI TestClient against a scratch SQLite
    file so state does not bleed between tests.
"""

from __future__ import annotations

import importlib.util
import os
import sys
import tempfile
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

REPO_ROOT = Path(__file__).resolve().parent.parent
COLLECTOR_PATH = REPO_ROOT / "sentinel-collector.py"

TEST_API_KEY = "unit-test-key-do-not-use-in-prod"


def _load_collector_with_env(env: dict[str, str]):
    """Load sentinel-collector.py as a fresh module under the given env.

    The collector snapshots os.environ into CONFIG at import time, so tests
    that want a different config MUST re-import under a modified environment.
    We wipe HBV_* first so leftover shell state can't contaminate the run.
    """
    for k in list(os.environ):
        if k.startswith("HBV_"):
            del os.environ[k]
    os.environ.update(env)

    # Drop any previously loaded copy so importlib re-executes the module body.
    sys.modules.pop("sentinel_collector_under_test", None)

    spec = importlib.util.spec_from_file_location(
        "sentinel_collector_under_test", str(COLLECTOR_PATH)
    )
    mod = importlib.util.module_from_spec(spec)
    sys.modules["sentinel_collector_under_test"] = mod
    spec.loader.exec_module(mod)  # type: ignore[union-attr]
    return mod


@pytest.fixture
def collector_auth_on(tmp_path):
    """A collector with auth enabled and a known API key."""
    db_path = tmp_path / "sentinel.db"
    mod = _load_collector_with_env({
        "HBV_HOST": "127.0.0.1",
        "HBV_DB_PATH": str(db_path),
        "HBV_API_KEY_REQUIRED": "true",
        "HBV_API_KEY": TEST_API_KEY,
        "HBV_ALLOWED_ORIGINS": "",
        "HBV_BEACON_MAX_SKEW": "300",
        # Give freshness a wide berth for tests that hand-craft timestamps.
        "HBV_RATE_LIMIT_REQUESTS": "10000",
        "HBV_RATE_LIMIT_WINDOW": "60",
    })
    with TestClient(mod.app) as client:
        yield mod, client


@pytest.fixture
def collector_auth_off(tmp_path):
    """A collector with auth explicitly disabled (opt-out path)."""
    db_path = tmp_path / "sentinel.db"
    mod = _load_collector_with_env({
        "HBV_HOST": "127.0.0.1",
        "HBV_DB_PATH": str(db_path),
        "HBV_API_KEY_REQUIRED": "false",
        "HBV_ALLOWED_ORIGINS": "",
        "HBV_BEACON_MAX_SKEW": "300",
        "HBV_RATE_LIMIT_REQUESTS": "10000",
        "HBV_RATE_LIMIT_WINDOW": "60",
    })
    with TestClient(mod.app) as client:
        yield mod, client


def _valid_beacon(agent_id: str = "test-agent", ts: int | None = None) -> dict:
    return {
        "agent_id": agent_id,
        "agent_type": "linux",
        "timestamp": ts if ts is not None else int(time.time()),
        "cpu_percent": 42.0,
        "memory_percent": 33.0,
        "disk_percent": 17.5,
    }


# ══════════════════════════════════════════════════════════════════════
# AUTH GATE — the exact regression the P0 fix must prevent
# ══════════════════════════════════════════════════════════════════════

class TestAuthGate:
    """Every data endpoint must be 401 without a valid X-API-Key."""

    PROTECTED_GET_ENDPOINTS = [
        "/api/agents",
        "/api/beacons/latest",
        "/api/beacons/test-agent",
        "/api/alerts",
        "/api/stats",
        "/metrics",
    ]

    @pytest.mark.parametrize("path", PROTECTED_GET_ENDPOINTS)
    def test_missing_key_is_401(self, collector_auth_on, path):
        _, client = collector_auth_on
        r = client.get(path)
        assert r.status_code == 401, f"{path} allowed anonymous access: {r.status_code} {r.text}"

    @pytest.mark.parametrize("path", PROTECTED_GET_ENDPOINTS)
    def test_wrong_key_is_401(self, collector_auth_on, path):
        _, client = collector_auth_on
        r = client.get(path, headers={"X-API-Key": "totally-wrong"})
        assert r.status_code == 401, f"{path} accepted wrong key: {r.status_code}"

    @pytest.mark.parametrize("path", PROTECTED_GET_ENDPOINTS)
    def test_valid_key_is_2xx(self, collector_auth_on, path):
        _, client = collector_auth_on
        r = client.get(path, headers={"X-API-Key": TEST_API_KEY})
        assert 200 <= r.status_code < 300, f"{path} rejected valid key: {r.status_code} {r.text}"

    def test_beacon_post_requires_key(self, collector_auth_on):
        _, client = collector_auth_on
        r = client.post("/api/beacon", json=_valid_beacon())
        assert r.status_code == 401

    def test_beacon_post_accepts_valid_key(self, collector_auth_on):
        _, client = collector_auth_on
        r = client.post(
            "/api/beacon",
            json=_valid_beacon(),
            headers={"X-API-Key": TEST_API_KEY},
        )
        assert r.status_code == 200, r.text
        assert r.json()["status"] == "success"

    def test_health_is_open(self, collector_auth_on):
        """Liveness probe must not require auth (load balancer contract)."""
        _, client = collector_auth_on
        r = client.get("/health")
        assert r.status_code == 200
        assert r.json()["status"] == "healthy"

    def test_dashboard_shell_disabled_by_default(self, collector_auth_on):
        """GET / must be 404 unless HBV_ENABLE_DASHBOARD=true (review R-02).

        The interactive shell is a credential-entry surface. Health-only
        public-route criterion means /health is the ONLY anonymous route
        under production defaults.
        """
        _, client = collector_auth_on
        r = client.get("/")
        assert r.status_code == 404, \
            f"dashboard shell should not exist under default config: {r.status_code}"

    def test_dashboard_shell_when_enabled_ships_no_secret(self, tmp_path):
        """When explicitly opted in, the shell still contains no secret."""
        mod = _load_collector_with_env({
            "HBV_HOST": "127.0.0.1",
            "HBV_DB_PATH": str(tmp_path / "sentinel.db"),
            "HBV_API_KEY_REQUIRED": "true",
            "HBV_API_KEY": TEST_API_KEY,
            "HBV_ENABLE_DASHBOARD": "true",
        })
        with TestClient(mod.app) as client:
            r = client.get("/")
            assert r.status_code == 200
            assert TEST_API_KEY not in r.text, "API key leaked into dashboard HTML"

    def test_auth_off_lets_reads_through(self, collector_auth_off):
        """Sanity: with auth explicitly disabled the reads work — but this is
        the operator opt-out path, not the default."""
        mod, client = collector_auth_off
        assert mod.CONFIG["api_key_required"] is False
        assert client.get("/api/stats").status_code == 200


# ══════════════════════════════════════════════════════════════════════
# CONFIG DEFAULTS — the "secure by default" contract
# ══════════════════════════════════════════════════════════════════════

class TestSecureDefaults:
    def test_defaults_are_secure(self, tmp_path):
        mod = _load_collector_with_env({"HBV_DB_PATH": str(tmp_path / "s.db")})
        assert mod.CONFIG["host"] == "127.0.0.1", "default bind must be loopback"
        assert mod.CONFIG["api_key_required"] is True, "auth must be on by default"
        assert mod.CONFIG["allowed_origins"] == [], "CORS must start empty"
        assert mod.API_KEY_IS_EPHEMERAL is True

    def test_cors_wildcard_disables_credentials(self, tmp_path):
        """Wildcard origins must NOT be paired with credentials — spec violation
        and a credential-leak shape if a browser honored it."""
        mod = _load_collector_with_env({
            "HBV_DB_PATH": str(tmp_path / "s.db"),
            "HBV_API_KEY_REQUIRED": "true",
            "HBV_API_KEY": TEST_API_KEY,
            "HBV_ALLOWED_ORIGINS": "*",
        })
        # Walk the FastAPI middleware stack for the CORS entry.
        cors = None
        for m in mod.app.user_middleware:
            if m.cls.__name__ == "CORSMiddleware":
                cors = m
                break
        assert cors is not None, "CORS middleware not installed"
        # allow_credentials is passed via kwargs on the Middleware wrapper.
        kwargs = getattr(cors, "kwargs", None) or getattr(cors, "options", {})
        assert kwargs.get("allow_credentials") is False, (
            "wildcard origins must force allow_credentials=False"
        )


# ══════════════════════════════════════════════════════════════════════
# REPLAY / FRESHNESS
# ══════════════════════════════════════════════════════════════════════

class TestReplayAndFreshness:
    def test_stale_beacon_rejected(self, collector_auth_on):
        mod, client = collector_auth_on
        stale = _valid_beacon(ts=int(time.time()) - mod.CONFIG["beacon_max_skew"] - 60)
        r = client.post("/api/beacon", json=stale, headers={"X-API-Key": TEST_API_KEY})
        assert r.status_code == 400, r.text

    def test_future_beacon_rejected(self, collector_auth_on):
        mod, client = collector_auth_on
        future = _valid_beacon(ts=int(time.time()) + mod.CONFIG["beacon_max_skew"] + 60)
        r = client.post("/api/beacon", json=future, headers={"X-API-Key": TEST_API_KEY})
        assert r.status_code == 400, r.text

    def test_duplicate_beacon_rejected(self, collector_auth_on):
        _, client = collector_auth_on
        b = _valid_beacon(agent_id="dupe-agent", ts=int(time.time()))
        r1 = client.post("/api/beacon", json=b, headers={"X-API-Key": TEST_API_KEY})
        assert r1.status_code == 200
        r2 = client.post("/api/beacon", json=b, headers={"X-API-Key": TEST_API_KEY})
        assert r2.status_code == 409, "verbatim replay must be rejected"


# ══════════════════════════════════════════════════════════════════════
# INPUT VALIDATION at the real endpoint
# ══════════════════════════════════════════════════════════════════════

class TestBeaconInputValidation:
    def test_bad_agent_id_rejected(self, collector_auth_on):
        _, client = collector_auth_on
        b = _valid_beacon(agent_id="host name")  # space forbidden
        r = client.post("/api/beacon", json=b, headers={"X-API-Key": TEST_API_KEY})
        assert r.status_code == 422

    def test_bad_agent_type_rejected(self, collector_auth_on):
        _, client = collector_auth_on
        b = _valid_beacon()
        b["agent_type"] = "solaris"
        r = client.post("/api/beacon", json=b, headers={"X-API-Key": TEST_API_KEY})
        assert r.status_code == 422

    def test_out_of_range_cpu_rejected(self, collector_auth_on):
        _, client = collector_auth_on
        b = _valid_beacon()
        b["cpu_percent"] = 150.0
        r = client.post("/api/beacon", json=b, headers={"X-API-Key": TEST_API_KEY})
        assert r.status_code == 422

    def test_agent_id_bad_chars_rejected(self, collector_auth_on):
        """Metacharacters must never reach the SQL layer — regex kicks them
        out at the handler with 400. (Path traversal via URL-encoded slashes
        is defeated one level earlier by Starlette's route matching.)"""
        _, client = collector_auth_on
        r = client.get(
            "/api/beacons/bad;drop",
            headers={"X-API-Key": TEST_API_KEY},
        )
        assert r.status_code == 400


# ══════════════════════════════════════════════════════════════════════
# END-TO-END: beacon → store → surface via authenticated reads
# ══════════════════════════════════════════════════════════════════════

class TestBeaconRoundTrip:
    def test_beacon_visible_on_authenticated_reads(self, collector_auth_on):
        _, client = collector_auth_on
        hdr = {"X-API-Key": TEST_API_KEY}
        b = _valid_beacon(agent_id="round-trip-agent", ts=int(time.time()))
        assert client.post("/api/beacon", json=b, headers=hdr).status_code == 200

        stats = client.get("/api/stats", headers=hdr).json()
        assert stats["agents"]["total"] >= 1
        assert stats["beacons"]["total"] >= 1

        agents = client.get("/api/agents", headers=hdr).json()["agents"]
        assert any(a["agent_id"] == "round-trip-agent" for a in agents)

        latest = client.get("/api/beacons/latest", headers=hdr).json()["beacons"]
        assert any(x.get("agent_id") == "round-trip-agent" for x in latest)

    def test_alert_generated_on_high_cpu(self, collector_auth_on):
        _, client = collector_auth_on
        hdr = {"X-API-Key": TEST_API_KEY}
        b = _valid_beacon(agent_id="hot-agent")
        b["cpu_percent"] = 99.0
        r = client.post("/api/beacon", json=b, headers=hdr)
        assert r.status_code == 200
        assert r.json()["alerts"] >= 1

        alerts = client.get("/api/alerts", headers=hdr).json()["alerts"]
        assert any(a["alert_type"] == "cpu_high" for a in alerts)


# ══════════════════════════════════════════════════════════════════════
# AGENT TRANSPORT — cleartext HTTP must be refused by default
# ══════════════════════════════════════════════════════════════════════

class TestAgentTransport:
    def _load_agent(self, env: dict[str, str]):
        for k in list(os.environ):
            if k.startswith("HBV_"):
                del os.environ[k]
        os.environ.update(env)
        sys.modules.pop("sentinel_agent_under_test", None)
        spec = importlib.util.spec_from_file_location(
            "sentinel_agent_under_test",
            str(REPO_ROOT / "sentinel-agent-linux.py"),
        )
        mod = importlib.util.module_from_spec(spec)
        sys.modules["sentinel_agent_under_test"] = mod
        spec.loader.exec_module(mod)  # type: ignore[union-attr]
        return mod

    def test_default_scheme_is_https(self):
        mod = self._load_agent({})
        assert mod.CONFIG["api_endpoint"].startswith("https://")
        assert mod.CONFIG["allow_insecure"] is False

    def test_cleartext_refused_by_default(self):
        mod = self._load_agent({
            "HBV_COLLECTOR_URL": "http://collector.local:8443/api/beacon",
        })
        # send_beacon_http should refuse without ever calling requests.post.
        called = {"n": 0}

        def guard(*_a, **_kw):
            called["n"] += 1
            raise AssertionError("requests.post must NOT be called for cleartext HTTP")

        mod.requests.post = guard  # type: ignore[assignment]
        assert mod.send_beacon_http({"agent_id": "x", "timestamp": 0}) is False
        assert called["n"] == 0

    def test_cleartext_allowed_with_opt_in(self):
        mod = self._load_agent({
            "HBV_COLLECTOR_URL": "http://collector.local:8443/api/beacon",
            "HBV_ALLOW_INSECURE": "true",
        })

        class FakeResp:
            status_code = 200

        seen = {}

        def capture(url, json=None, timeout=None, headers=None, verify=None):
            seen.update(url=url, verify=verify, headers=headers)
            return FakeResp()

        mod.requests.post = capture  # type: ignore[assignment]
        assert mod.send_beacon_http({"agent_id": "x", "timestamp": 0}) is True
        assert seen["url"].startswith("http://")
        # verify is meaningless for http:// but must be a bool, not raise.
        assert seen["verify"] is False


# ══════════════════════════════════════════════════════════════════════
# RATE LIMITING — the enforcement path, not a private copy
# ══════════════════════════════════════════════════════════════════════

class TestRateLimit:
    """Prove the real /api/beacon endpoint returns 429 past the window."""

    def _tight_client(self, tmp_path):
        db_path = tmp_path / "sentinel.db"
        mod = _load_collector_with_env({
            "HBV_HOST": "127.0.0.1",
            "HBV_DB_PATH": str(db_path),
            "HBV_API_KEY_REQUIRED": "true",
            "HBV_API_KEY": TEST_API_KEY,
            "HBV_ALLOWED_ORIGINS": "",
            "HBV_BEACON_MAX_SKEW": "300",
            "HBV_RATE_LIMIT_REQUESTS": "3",       # tight for the test
            "HBV_RATE_LIMIT_WINDOW": "60",
        })
        return mod, TestClient(mod.app)

    def test_beacon_endpoint_returns_429_past_limit(self, tmp_path):
        mod, client = self._tight_client(tmp_path)
        with client:
            hdr = {"X-API-Key": TEST_API_KEY}
            # 3 unique beacons succeed (each a distinct ts to dodge replay).
            base = int(time.time())
            for i in range(3):
                b = _valid_beacon(agent_id=f"rate-agent-{i}", ts=base + i)
                r = client.post("/api/beacon", json=b, headers=hdr)
                assert r.status_code == 200, f"req {i}: {r.status_code} {r.text}"
            # 4th must be rate-limited.
            b = _valid_beacon(agent_id="rate-agent-x", ts=base + 99)
            r = client.post("/api/beacon", json=b, headers=hdr)
            assert r.status_code == 429, r.text


# ══════════════════════════════════════════════════════════════════════
# PERSISTENCE FAILURE — S-03: no phantom "success" on DB failure
# ══════════════════════════════════════════════════════════════════════

class TestPersistenceFailure:
    def test_db_failure_returns_5xx_and_no_phantom_success(self, collector_auth_on):
        """Injected DB failure MUST NOT return 200 and MUST NOT poison replay."""
        mod, client = collector_auth_on
        hdr = {"X-API-Key": TEST_API_KEY}

        # Force store_beacon to fail once.
        real = mod.db.store_beacon
        failures = {"n": 0}

        def flaky(beacon_dict):
            if failures["n"] == 0:
                failures["n"] += 1
                return False
            return real(beacon_dict)

        mod.db.store_beacon = flaky

        b = _valid_beacon(agent_id="fail-agent", ts=int(time.time()))
        r1 = client.post("/api/beacon", json=b, headers=hdr)
        assert r1.status_code >= 500, f"expected 5xx, got {r1.status_code}"

        # The failed beacon MUST NOT appear in reads.
        latest = client.get("/api/beacons/latest", headers=hdr).json()["beacons"]
        assert not any(x.get("agent_id") == "fail-agent" for x in latest)

        # The HONEST retry of the same payload must be accepted, not rejected
        # as a duplicate (the earlier version would have poisoned the replay
        # window — review finding S-03).
        r2 = client.post("/api/beacon", json=b, headers=hdr)
        assert r2.status_code == 200, r2.text
        latest2 = client.get("/api/beacons/latest", headers=hdr).json()["beacons"]
        assert any(x.get("agent_id") == "fail-agent" for x in latest2)


# ══════════════════════════════════════════════════════════════════════
# PUBLIC-ROUTE INVENTORY — S-06: only /health and / anonymous by default
# ══════════════════════════════════════════════════════════════════════

class TestPublicRouteInventory:
    def test_only_health_is_anonymous_by_default(self, collector_auth_on):
        """Health-only public-route criterion (review R-02).

        Under production defaults, every parameter-free GET route other than
        /health must be non-2xx to an anonymous caller. That specifically
        includes / (dashboard shell disabled by default now).
        """
        mod, client = collector_auth_on
        OPEN = {"/health"}

        anon_ok = []
        for route in mod.app.router.routes:
            path = getattr(route, "path", None)
            methods = getattr(route, "methods", None) or {"GET"}
            if not path or path in OPEN:
                continue
            if "{" in path:  # parameterized — TestClient can't call raw
                continue
            if "GET" not in methods:
                continue
            r = client.get(path)
            if 200 <= r.status_code < 300:
                anon_ok.append(f"{path} → {r.status_code}")

        assert not anon_ok, f"anonymous access allowed on: {anon_ok}"

    def test_docs_routes_disabled_by_default(self, collector_auth_on):
        _, client = collector_auth_on
        for p in ("/docs", "/redoc", "/openapi.json"):
            r = client.get(p)
            assert r.status_code == 404, f"{p} should be disabled (got {r.status_code})"

    def test_docs_routes_enabled_via_opt_in(self, tmp_path):
        """Opt-in path exists: HBV_ENABLE_DOCS=true exposes /docs & /openapi.json."""
        mod = _load_collector_with_env({
            "HBV_DB_PATH": str(tmp_path / "s.db"),
            "HBV_API_KEY_REQUIRED": "true",
            "HBV_API_KEY": TEST_API_KEY,
            "HBV_ENABLE_DOCS": "true",
        })
        with TestClient(mod.app) as client:
            for p in ("/docs", "/openapi.json"):
                assert client.get(p).status_code == 200


# ══════════════════════════════════════════════════════════════════════
# WINDOWS AGENT — config-policy assertions parsed from the PS1 source
# ══════════════════════════════════════════════════════════════════════
# S-01 acceptance: the Windows agent MUST default to HTTPS, refuse cleartext
# unless HBV_ALLOW_INSECURE=true, and preserve TLS verification. We can't run
# PowerShell here, so we assert the exact policy shape in source form.

class TestWindowsAgentPolicy:
    @pytest.fixture(scope="class")
    def ps1_source(self):
        return (REPO_ROOT / "Sentinel-Agent-Windows.ps1").read_text()

    def test_default_endpoint_is_https(self, ps1_source):
        # Look for the config default explicitly, ignoring documentation lines.
        assert 'Get-EnvOrDefault -Name "HBV_COLLECTOR_URL" -Default "https://' in ps1_source, \
            "Windows agent default APIEndpoint must be https://"
        assert 'Get-EnvOrDefault -Name "HBV_COLLECTOR_URL" -Default "http://' not in ps1_source, \
            "Windows agent default APIEndpoint must not be http://"

    def test_allow_insecure_defaults_false(self, ps1_source):
        assert 'Get-EnvBoolOrDefault -Name "HBV_ALLOW_INSECURE" -Default $false' in ps1_source, \
            "AllowInsecure must default to $false"

    def test_transport_policy_gate_exists(self, ps1_source):
        # The refusal path must be present and must be the code path taken by
        # Send-BeaconHTTP before Invoke-RestMethod.
        assert "function Test-TransportPolicy" in ps1_source
        assert "Refusing to send beacon over plaintext HTTP" in ps1_source
        assert "Test-TransportPolicy -Endpoint $script:Config.APIEndpoint" in ps1_source

    def test_tls_verify_default_is_on(self, ps1_source):
        # The SkipCertificateCheck path is guarded by an explicit opt-in string.
        assert 'HBV_TLS_CA_BUNDLE=false' in ps1_source or \
               'TLSCaBundle.Trim().ToLowerInvariant() -eq "false"' in ps1_source, \
            "TLS verify skip must be gated on the literal HBV_TLS_CA_BUNDLE=false"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
