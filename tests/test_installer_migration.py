#!/usr/bin/env python3
"""
Installer credential-reconciliation tests (review finding H-01).

Exercises scripts/reconcile-credentials.sh under isolated fixtures for every
branch the reviewer named: fresh install, v1.1.1-style upgrade (env file
exists, key file missing), reinstall (both files agree), partial (only key
file exists), and MISMATCH (both files exist and disagree). No systemd, no
Python collector, no real /etc paths — everything runs in a per-test tmpdir.
"""

from __future__ import annotations

import os
import stat
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPT = REPO_ROOT / "scripts" / "reconcile-credentials.sh"
TEMPLATE = REPO_ROOT / "scripts" / "collector.env.template"


def _run(env_file: Path, key_file: Path):
    """Invoke the reconciler under a scrubbed environment; return CompletedProcess."""
    env = {
        "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
        "ENV_FILE": str(env_file),
        "KEY_FILE": str(key_file),
        "TEMPLATE": str(TEMPLATE),
    }
    return subprocess.run(
        ["bash", str(SCRIPT)],
        env=env,
        capture_output=True,
        text=True,
    )


def _read_env_key(env_file: Path) -> str | None:
    """Match the reconciler's own extraction to keep the test faithful."""
    if not env_file.exists():
        return None
    for line in env_file.read_text().splitlines():
        s = line.strip()
        if s.startswith("#") or "=" not in s:
            continue
        k, _, v = s.partition("=")
        if k.strip() == "HBV_API_KEY":
            v = v.strip().strip('"').strip("'")
            return v
    return None


def _write_env(env_file: Path, key_value: str | None):
    """Emit a minimal v1.1.1-style collector.env fixture."""
    lines = [
        "HBV_HOST=127.0.0.1",
        "HBV_PORT=8443",
        "HBV_API_KEY_REQUIRED=true",
    ]
    if key_value is not None:
        lines.append(f"HBV_API_KEY={key_value}")
    lines.append("")
    env_file.write_text("\n".join(lines))


# ══════════════════════════════════════════════════════════════════════
# FRESH INSTALL — neither file exists
# ══════════════════════════════════════════════════════════════════════

class TestFreshInstall:
    def test_generates_both_files_with_matching_key(self, tmp_path):
        env_file = tmp_path / "collector.env"
        key_file = tmp_path / "api.key"

        r = _run(env_file, key_file)
        assert r.returncode == 0, f"stderr={r.stderr}\nstdout={r.stdout}"

        assert env_file.exists() and key_file.exists()
        env_key = _read_env_key(env_file)
        key_value = key_file.read_text().strip()

        assert env_key and env_key == key_value, \
            f"fresh install produced diverging credentials: env={env_key!r} key={key_value!r}"
        # KEY_FILE must be 0600.
        assert stat.S_IMODE(key_file.stat().st_mode) == 0o600


# ══════════════════════════════════════════════════════════════════════
# v1.1.1 UPGRADE — the exact H-01 case
# ══════════════════════════════════════════════════════════════════════

class TestV111Upgrade:
    def test_env_key_populates_missing_key_file(self, tmp_path):
        env_file = tmp_path / "collector.env"
        key_file = tmp_path / "api.key"
        expected = "existing-v1_1_1-key-xyz"
        _write_env(env_file, expected)
        assert not key_file.exists()

        r = _run(env_file, key_file)
        assert r.returncode == 0, f"stderr={r.stderr}"

        # KEY_FILE must contain the ENV_FILE key — not a fresh random value.
        assert key_file.read_text().strip() == expected, \
            "upgrade regenerated api.key instead of populating it from collector.env"
        # ENV_FILE must be unchanged in its authoritative field.
        assert _read_env_key(env_file) == expected
        assert stat.S_IMODE(key_file.stat().st_mode) == 0o600

    def test_env_file_present_but_key_unset_refuses(self, tmp_path):
        env_file = tmp_path / "collector.env"
        key_file = tmp_path / "api.key"
        _write_env(env_file, None)  # HBV_API_KEY not set at all

        r = _run(env_file, key_file)
        assert r.returncode != 0, "must refuse to synthesize a key silently"
        assert not key_file.exists(), \
            "must NOT create api.key when the operator's env has no HBV_API_KEY"


# ══════════════════════════════════════════════════════════════════════
# REINSTALL — both agree
# ══════════════════════════════════════════════════════════════════════

class TestReinstallIdempotent:
    def test_agreeing_files_produce_no_changes(self, tmp_path):
        env_file = tmp_path / "collector.env"
        key_file = tmp_path / "api.key"
        key = "matching-key-abc"
        _write_env(env_file, key)
        key_file.write_text(key + "\n")
        os.chmod(key_file, 0o600)

        env_before = env_file.read_text()
        key_before = key_file.read_text()

        r = _run(env_file, key_file)
        assert r.returncode == 0

        assert env_file.read_text() == env_before, "reinstall mutated ENV_FILE"
        assert key_file.read_text() == key_before, "reinstall mutated KEY_FILE"


# ══════════════════════════════════════════════════════════════════════
# PARTIAL — only KEY_FILE exists
# ══════════════════════════════════════════════════════════════════════

class TestPartialKeyOnly:
    def test_key_file_alone_produces_matching_env(self, tmp_path):
        env_file = tmp_path / "collector.env"
        key_file = tmp_path / "api.key"
        key = "just-the-key-file"
        key_file.write_text(key + "\n")
        os.chmod(key_file, 0o600)

        r = _run(env_file, key_file)
        assert r.returncode == 0

        assert env_file.exists()
        assert _read_env_key(env_file) == key


# ══════════════════════════════════════════════════════════════════════
# MISMATCH — the reviewer's specific fail-safe requirement
# ══════════════════════════════════════════════════════════════════════

class TestMismatchIsFatal:
    def test_disagreeing_files_stop_installer(self, tmp_path):
        env_file = tmp_path / "collector.env"
        key_file = tmp_path / "api.key"
        _write_env(env_file, "env-file-value")
        key_file.write_text("key-file-value\n")
        os.chmod(key_file, 0o600)

        env_before = env_file.read_text()
        key_before = key_file.read_text()

        r = _run(env_file, key_file)
        assert r.returncode != 0, "mismatch must be fatal"
        assert "MISMATCH" in (r.stdout + r.stderr)

        # Neither file may be mutated when the reconciler refuses.
        assert env_file.read_text() == env_before
        assert key_file.read_text() == key_before


# ══════════════════════════════════════════════════════════════════════
# PRINTED INSTRUCTIONS — the reviewer's operator-facing acceptance
# ══════════════════════════════════════════════════════════════════════

class TestActiveKeyInvariant:
    """
    The installer's post-install instructions tell the operator to read
    $KEY_FILE for the active key. The reconciler must guarantee that
    reading $KEY_FILE always gives the value the running collector will
    read from $ENV_FILE — for every branch that produces a successful
    exit, the two must agree.
    """
    @pytest.mark.parametrize("scenario", ["fresh", "upgrade", "reinstall", "partial"])
    def test_key_file_equals_env_file_after_reconcile(self, tmp_path, scenario):
        env_file = tmp_path / "collector.env"
        key_file = tmp_path / "api.key"

        if scenario == "fresh":
            pass  # neither exists
        elif scenario == "upgrade":
            _write_env(env_file, "upgrade-active-key")
        elif scenario == "reinstall":
            k = "reinstall-key"
            _write_env(env_file, k)
            key_file.write_text(k + "\n")
            os.chmod(key_file, 0o600)
        elif scenario == "partial":
            key_file.write_text("partial-key\n")
            os.chmod(key_file, 0o600)

        r = _run(env_file, key_file)
        assert r.returncode == 0, f"scenario={scenario}: stderr={r.stderr}"

        env_key = _read_env_key(env_file)
        key_value = key_file.read_text().strip()
        assert env_key == key_value, \
            f"scenario={scenario}: ENV_FILE and KEY_FILE diverge ({env_key!r} vs {key_value!r})"


# ══════════════════════════════════════════════════════════════════════
# INSTALLER WRAPPER — propagates the reconciler's exit code (review M-01)
# ══════════════════════════════════════════════════════════════════════

WRAPPER = REPO_ROOT / "scripts" / "invoke-reconciler.sh"


def _run_wrapper(env_file: Path, key_file: Path, fake_reconciler: Path):
    return subprocess.run(
        ["bash", str(WRAPPER)],
        env={
            "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
            "ENV_FILE": str(env_file),
            "KEY_FILE": str(key_file),
            "TEMPLATE": str(TEMPLATE),
            "RECONCILER": str(fake_reconciler),
        },
        capture_output=True,
        text=True,
    )


def _stub_reconciler(tmp_path: Path, exit_code: int) -> Path:
    """A stubbed reconciler that exits with the given code, so the wrapper
    can be tested in isolation without any real credential state.
    """
    p = tmp_path / f"fake-reconciler-{exit_code}.sh"
    p.write_text(f"#!/bin/bash\necho stubbed-refusal >&2\nexit {exit_code}\n")
    p.chmod(0o755)
    return p


class TestInstallerWrapperExitCode:
    """
    Direct regression for M-01: the wrapper MUST propagate the reconciler's
    original nonzero status. In v1.1.3 the installer used `if ! reconciler`
    which captured 0 in the failure branch and returned success on refusal.
    """

    def test_success_propagates_zero(self, tmp_path):
        stub = _stub_reconciler(tmp_path, 0)
        r = _run_wrapper(tmp_path / "env", tmp_path / "key", stub)
        assert r.returncode == 0, f"success wrapper should exit 0, got {r.returncode}"

    def test_exit_2_propagates_exactly(self, tmp_path):
        stub = _stub_reconciler(tmp_path, 2)
        r = _run_wrapper(tmp_path / "env", tmp_path / "key", stub)
        assert r.returncode == 2, \
            f"wrapper must propagate exit 2 verbatim (M-01 regression), got {r.returncode}"
        # Failure message must appear so operators know what happened.
        assert "NOT restarting the collector" in r.stdout

    def test_exit_3_propagates_exactly(self, tmp_path):
        stub = _stub_reconciler(tmp_path, 3)
        r = _run_wrapper(tmp_path / "env", tmp_path / "key", stub)
        assert r.returncode == 3, \
            f"wrapper must propagate exit 3 verbatim (M-01 regression), got {r.returncode}"


class TestInstallerWrapperNeverPrintsExitZeroOnFailure:
    """The 'exit=0 on failure' pattern is the exact release-blocker M-01."""

    @pytest.mark.parametrize("code", [1, 2, 3, 42, 77])
    def test_arbitrary_nonzero_propagated(self, tmp_path, code):
        stub = _stub_reconciler(tmp_path, code)
        r = _run_wrapper(tmp_path / "env", tmp_path / "key", stub)
        assert r.returncode == code
        assert "exit=0" not in r.stdout, \
            "wrapper printed 'exit=0' after a nonzero reconciler — M-01 regression"


# ══════════════════════════════════════════════════════════════════════
# SECRET-SAFE DIAGNOSTICS — mismatch output must not leak keys (M-02)
# ══════════════════════════════════════════════════════════════════════

class TestMismatchOutputHygiene:
    def test_mismatch_does_not_disclose_either_credential_value(self, tmp_path):
        """
        Reviewer M-02: mismatch printed both full API keys to stdout, which
        installer captures typically leak into cloud-init/CI/config-mgmt logs.
        The remediation should print only paths, presence, length, and a
        non-reversible short fingerprint — never the values themselves.
        """
        env_file = tmp_path / "collector.env"
        key_file = tmp_path / "api.key"

        # Unique marker secrets — if either appears in output verbatim, the
        # remediation regressed and the reviewer's exact defect is back.
        env_marker = "MARKER_ENV_SECRET_9f3c7b1e2d4a8f60_do_not_leak"
        key_marker = "MARKER_KEY_SECRET_a72d1c9e6f83b504_do_not_leak"
        _write_env(env_file, env_marker)
        key_file.write_text(key_marker + "\n")
        os.chmod(key_file, 0o600)

        r = _run(env_file, key_file)
        assert r.returncode != 0, "mismatch must remain fatal"

        combined = r.stdout + r.stderr
        assert env_marker not in combined, \
            "env-file API key value leaked into mismatch diagnostics (M-02 regression)"
        assert key_marker not in combined, \
            "api.key value leaked into mismatch diagnostics (M-02 regression)"

        # Comparison aid remains useful: file paths and a short fingerprint
        # should be present so the operator can act.
        assert str(env_file) in combined
        assert str(key_file) in combined
        assert "fingerprint=" in combined
        assert "MISMATCH" in combined

    def test_mismatch_fingerprints_differ_for_different_secrets(self, tmp_path):
        """Sanity: the fingerprint is deterministic and different for
        different values — otherwise the aid doesn't help the operator."""
        env_file = tmp_path / "collector.env"
        key_file = tmp_path / "api.key"
        _write_env(env_file, "value-one")
        key_file.write_text("value-two\n")
        os.chmod(key_file, 0o600)

        r = _run(env_file, key_file)
        combined = r.stdout + r.stderr
        # Extract the two fingerprint fields.
        import re
        fps = re.findall(r"fingerprint=([0-9a-f]+)", combined)
        assert len(fps) == 2, f"expected two fingerprint values, got {fps!r}"
        assert fps[0] != fps[1], "fingerprints must distinguish differing values"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
