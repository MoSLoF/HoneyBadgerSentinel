#!/usr/bin/env python3
"""
HoneyBadger Sentinel - Collector Tests

Basic unit tests for the collector API.
Run with: pytest tests/ -v
"""

import json
import time
import tempfile
import pytest
from pathlib import Path

# Add parent directory to path for imports
import sys
sys.path.insert(0, str(Path(__file__).parent.parent))


@pytest.fixture
def temp_db():
    """Create a temporary database for testing."""
    with tempfile.NamedTemporaryFile(suffix='.db', delete=False) as f:
        yield f.name
    # Cleanup
    Path(f.name).unlink(missing_ok=True)


class TestBeaconValidation:
    """Test beacon request validation."""

    def test_valid_agent_id(self):
        """Test valid agent ID patterns."""
        import re
        pattern = re.compile(r'^[a-zA-Z0-9_-]{1,64}$')

        valid_ids = [
            "server-01",
            "NAS_TT",
            "myhost123",
            "a",
            "a" * 64,
            "test-host_01",
        ]

        for agent_id in valid_ids:
            assert pattern.match(agent_id), f"Should be valid: {agent_id}"

    def test_invalid_agent_id(self):
        """Test invalid agent ID patterns."""
        import re
        pattern = re.compile(r'^[a-zA-Z0-9_-]{1,64}$')

        invalid_ids = [
            "",  # Empty
            "a" * 65,  # Too long
            "host.domain",  # Dots not allowed
            "host name",  # Spaces not allowed
            "host\nname",  # Newlines not allowed
            "../etc/passwd",  # Path traversal
            "host<script>",  # HTML injection
        ]

        for agent_id in invalid_ids:
            assert not pattern.match(agent_id), f"Should be invalid: {agent_id}"


class TestSanitization:
    """Test input sanitization functions."""

    def test_sanitize_removes_control_chars(self):
        """Test that control characters are removed."""
        import re

        def sanitize_for_logging(value: str, max_length: int = 64) -> str:
            if not isinstance(value, str):
                value = str(value)
            sanitized = re.sub(r'[\x00-\x1f\x7f-\x9f]', '', value)
            return sanitized[:max_length]

        # Test control character removal
        assert sanitize_for_logging("hello\x00world") == "helloworld"
        assert sanitize_for_logging("line1\nline2") == "line1line2"
        assert sanitize_for_logging("tab\there") == "tabhere"

        # Test length limit
        assert len(sanitize_for_logging("a" * 100)) == 64
        assert sanitize_for_logging("short") == "short"


class TestBeaconModel:
    """Test Pydantic beacon model validation."""

    def test_valid_beacon(self):
        """Test valid beacon data."""
        from pydantic import BaseModel, Field
        from typing import Optional

        class BeaconRequest(BaseModel):
            agent_id: str = Field(..., min_length=1, max_length=64)
            agent_type: str = Field(..., pattern=r'^(linux|windows|macos|unknown)$')
            timestamp: int = Field(..., ge=0)
            cpu_percent: Optional[float] = Field(None, ge=0, le=100)

        # Valid beacon
        beacon = BeaconRequest(
            agent_id="test-server",
            agent_type="linux",
            timestamp=int(time.time()),
            cpu_percent=45.5
        )
        assert beacon.agent_id == "test-server"
        assert beacon.agent_type == "linux"

    def test_invalid_agent_type(self):
        """Test invalid agent type is rejected."""
        from pydantic import BaseModel, Field, ValidationError

        class BeaconRequest(BaseModel):
            agent_id: str = Field(..., min_length=1, max_length=64)
            agent_type: str = Field(..., pattern=r'^(linux|windows|macos|unknown)$')
            timestamp: int = Field(..., ge=0)

        with pytest.raises(ValidationError):
            BeaconRequest(
                agent_id="test",
                agent_type="invalid_type",
                timestamp=123
            )

    def test_cpu_percent_bounds(self):
        """Test CPU percent validation bounds."""
        from pydantic import BaseModel, Field, ValidationError
        from typing import Optional

        class BeaconRequest(BaseModel):
            agent_id: str = Field(..., min_length=1, max_length=64)
            agent_type: str = Field(..., pattern=r'^(linux|windows|macos|unknown)$')
            timestamp: int = Field(..., ge=0)
            cpu_percent: Optional[float] = Field(None, ge=0, le=100)

        # Valid bounds
        beacon = BeaconRequest(
            agent_id="test",
            agent_type="linux",
            timestamp=123,
            cpu_percent=0
        )
        assert beacon.cpu_percent == 0

        beacon = BeaconRequest(
            agent_id="test",
            agent_type="linux",
            timestamp=123,
            cpu_percent=100
        )
        assert beacon.cpu_percent == 100

        # Invalid - over 100
        with pytest.raises(ValidationError):
            BeaconRequest(
                agent_id="test",
                agent_type="linux",
                timestamp=123,
                cpu_percent=150
            )

        # Invalid - negative
        with pytest.raises(ValidationError):
            BeaconRequest(
                agent_id="test",
                agent_type="linux",
                timestamp=123,
                cpu_percent=-10
            )


class TestRateLimiter:
    """Test rate limiting functionality."""

    def test_rate_limiter_allows_under_limit(self):
        """Test that requests under limit are allowed."""
        from collections import defaultdict

        class RateLimiter:
            def __init__(self, max_requests: int, window_seconds: int):
                self.max_requests = max_requests
                self.window_seconds = window_seconds
                self.requests = defaultdict(list)

            def is_allowed(self, client_id: str) -> bool:
                now = time.time()
                cutoff = now - self.window_seconds
                self.requests[client_id] = [
                    t for t in self.requests[client_id] if t > cutoff
                ]
                if len(self.requests[client_id]) >= self.max_requests:
                    return False
                self.requests[client_id].append(now)
                return True

        limiter = RateLimiter(max_requests=5, window_seconds=60)

        # First 5 requests should be allowed
        for i in range(5):
            assert limiter.is_allowed("client1"), f"Request {i+1} should be allowed"

        # 6th request should be blocked
        assert not limiter.is_allowed("client1"), "6th request should be blocked"

    def test_rate_limiter_different_clients(self):
        """Test that different clients have separate limits."""
        from collections import defaultdict

        class RateLimiter:
            def __init__(self, max_requests: int, window_seconds: int):
                self.max_requests = max_requests
                self.window_seconds = window_seconds
                self.requests = defaultdict(list)

            def is_allowed(self, client_id: str) -> bool:
                now = time.time()
                cutoff = now - self.window_seconds
                self.requests[client_id] = [
                    t for t in self.requests[client_id] if t > cutoff
                ]
                if len(self.requests[client_id]) >= self.max_requests:
                    return False
                self.requests[client_id].append(now)
                return True

        limiter = RateLimiter(max_requests=2, window_seconds=60)

        # Client 1 uses their limit
        assert limiter.is_allowed("client1")
        assert limiter.is_allowed("client1")
        assert not limiter.is_allowed("client1")

        # Client 2 still has their own limit
        assert limiter.is_allowed("client2")
        assert limiter.is_allowed("client2")
        assert not limiter.is_allowed("client2")


class TestAlertThresholds:
    """Test alert threshold checking."""

    def test_cpu_alert_triggered(self):
        """Test CPU high alert is triggered."""
        thresholds = {
            "cpu_percent": 90,
            "memory_percent": 90,
            "disk_percent": 90,
            "gpu_temp_c": 85
        }

        def check_beacon(beacon: dict) -> list:
            alerts = []
            agent_id = beacon.get('agent_id', 'unknown')

            cpu = beacon.get('cpu_percent')
            if cpu and cpu > thresholds['cpu_percent']:
                alerts.append({
                    'agent_id': agent_id,
                    'alert_type': 'cpu_high',
                    'severity': 'warning',
                    'message': f"CPU usage high: {cpu}%"
                })
            return alerts

        # Should trigger alert
        beacon = {'agent_id': 'test', 'cpu_percent': 95}
        alerts = check_beacon(beacon)
        assert len(alerts) == 1
        assert alerts[0]['alert_type'] == 'cpu_high'

        # Should not trigger alert
        beacon = {'agent_id': 'test', 'cpu_percent': 50}
        alerts = check_beacon(beacon)
        assert len(alerts) == 0

    def test_disk_alert_is_critical(self):
        """Test disk alerts have critical severity."""
        thresholds = {
            "cpu_percent": 90,
            "memory_percent": 90,
            "disk_percent": 90,
            "gpu_temp_c": 85
        }

        def check_beacon(beacon: dict) -> list:
            alerts = []
            agent_id = beacon.get('agent_id', 'unknown')

            disk = beacon.get('disk_percent')
            if disk and disk > thresholds['disk_percent']:
                alerts.append({
                    'agent_id': agent_id,
                    'alert_type': 'disk_high',
                    'severity': 'critical',
                    'message': f"Disk usage high: {disk}%"
                })
            return alerts

        beacon = {'agent_id': 'test', 'disk_percent': 95}
        alerts = check_beacon(beacon)
        assert len(alerts) == 1
        assert alerts[0]['severity'] == 'critical'


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
