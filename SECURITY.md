# Security posture — HoneyBadger Sentinel

## v1.1.4 hardening summary (follow-up review remediation)

v1.1.4 closes the three Moderate release-quality defects the v1.1.3 review
identified — all three of which were introduced by remediation work in
prior rounds, and every one flagged as reviewer-P0-before-hardened.
A-01 remains the accepted shared-key / single-worker limitation.

### What changed in v1.1.4

**M-01 — Installer wrapper propagates the reconciler's exit code.**

The v1.1.3 installer used `if ! reconciler; then rc=$?; ...; exit $rc`.
Inside the `if !` branch, `$?` is the status of the *negated* command,
which is 0 whenever the reconciler failed. The wrapper printed a refusal
message and then exited 0 — automation received a false success signal on
a refused install. The reviewer reproduced this directly and named it
M-01.

Remediation:

- All installer→reconciler invocation logic moved into
  `scripts/invoke-reconciler.sh`. This wrapper uses the correct
  `if reconciler; then ...; else rc=$?; ...; exit "$rc"; fi` pattern —
  `$?` inside `else` IS the reconciler's original exit code, because
  that's what triggered entry into the else branch.
- `install-collector.sh` calls the wrapper and propagates its exit
  status; on nonzero it exits before ever reaching `systemctl`.
- `tests/test_installer_migration.py::TestInstallerWrapperExitCode` and
  `TestInstallerWrapperNeverPrintsExitZeroOnFailure` cover this: the
  wrapper is invoked against stubbed reconcilers that exit 1, 2, 3, 42,
  and 77, and the tests assert the wrapper's exit code equals the stub's
  and that the string `"exit=0"` never appears after a nonzero refusal.
  Seven parameterized cases.

**M-02 — Mismatch diagnostics no longer disclose credential values.**

The v1.1.3 reconciler printed both full API keys to stdout on a
credential mismatch. Installer output is captured by cloud-init,
config-management logs, CI runners, and interactive terminals — all of
which have broader read/retain boundaries than 0600 secret files. The
reviewer reproduced the leak with marker secrets.

Remediation:

- `reconcile-credentials.sh` now prints file paths, presence, length,
  and a SHA-256 fingerprint truncated to 12 hex characters — enough to
  distinguish two known values, nowhere near enough to reconstruct
  either.
- Explicit "credential values are NOT printed" warning in the mismatch
  message so an operator debugging in a shell doesn't wonder what to
  compare.
- `tests/test_installer_migration.py::TestMismatchOutputHygiene` uses
  marker secrets (`MARKER_ENV_SECRET_…`, `MARKER_KEY_SECRET_…`) and
  asserts that neither appears verbatim in the reconciler's stdout or
  stderr on a mismatch. A companion test verifies the fingerprint field
  is present, deterministic, and distinguishes differing values.

**M-03 — Pester loader scoping fixed.**

The v1.1.3 test file defined `Load-Agent` as a function and
dot-sourced `Sentinel-Agent-Windows.ps1` inside that function. Dot-sourcing
imports names into the *current* scope — which was `Load-Agent`'s function
scope. When Load-Agent returned, `Test-TransportPolicy`, `Send-BeaconHTTP`,
and `$script:Config` were no longer callable from the enclosing test
scope. The reviewer flagged this on the reasonable expectation that the
Windows CI job would fail before exercising any behavior.

Remediation:

- New `tests/AgentUnderTest.psm1` — a thin module wrapper that
  dot-sources the agent at module-import time, importing its functions
  and `$script:Config` into the module's own scope.
- `tests/agent-windows.Tests.ps1` rewritten to use `Import-Module
  -Force` per test (so a fresh `$script:Config` is built from the just-
  set `HBV_*` env vars) and `InModuleScope AgentUnderTest { ... }` to
  reach `$script:Config` and to install `Mock`s that the module's own
  `Invoke-RestMethod` calls actually see. This is the pattern the
  reviewer explicitly recommended: "a dedicated module and
  Import-Module -Force."
- CI workflow (`.github/workflows/ci.yml`) unchanged in structure —
  same SHA-pinned actions, same `windows-latest` Pester job, same
  NUnit-XML artifact upload — but now points at the corrected
  `agent-windows.Tests.ps1`.

### A-01 — Retained accepted limitation

Unchanged from v1.1.2/v1.1.3. Same posture: collector-wide shared key,
process-local `ReplayGuard`, single-worker uvicorn only. Full rationale
in the v1.1.2 section below.

### Total test count

**67 Python tests locally (was 57).** Ten new tests in
`test_installer_migration.py` cover M-01 and M-02 directly. Full Pester
suite in `tests/agent-windows.Tests.ps1` for the Windows runner. Local
run: `67 passed in 3.01s`.

### Upgrade impact (v1.1.3 → v1.1.4)

Drop-in for any v1.1.3 install. Installer changes are transparent to
running services (the wrapper is a new file; the installer just calls it
now). Reconciler mismatch behavior remains "refuse, exit nonzero, do NOT
restart" — the difference is that the message no longer contains
either credential value, and the installer's exit code now correctly
reflects the reconciler's original refusal status.

## v1.1.3 hardening summary (follow-up review remediation)

v1.1.3 closes the single new High the v1.1.2 follow-up review flagged
(H-01, installer credential divergence), closes R-02 (anonymous dashboard
shell), and lands the executable PowerShell coverage the reviewer asked for
under R-01. A-01 (shared-key/single-process replay) remains an accepted,
documented limitation on the same terms as v1.1.2.

### What changed in v1.1.3

**H-01 — Installer credential reconciliation (High).**

The v1.1.2 installer created `/etc/hbv-sentinel/api.key` whenever that file
was missing but only wrote `collector.env` when THAT was missing. On a
v1.1.1 upgrade (env file present, key file absent), the installer generated
a fresh random key into `api.key`, preserved the old env-file key, and told
operators to use the new key file — which was wrong. That's fixed:

- All credential logic moved out of `install-collector.sh` into a discrete
  helper: `scripts/reconcile-credentials.sh`. The installer just calls it.
- `collector.env` is authoritative. The reconciler handles five distinct
  branches with different behaviour:
  - **fresh** (neither exists) → generate one token, write BOTH.
  - **upgrade** (env exists, key missing) → populate `api.key` from the
    env file's `HBV_API_KEY`.
  - **reinstall** (both exist and agree) → no changes.
  - **partial-key-only** (key exists, env missing) → materialize env file
    from the key file.
  - **MISMATCH** (both exist and disagree) → refuse, exit nonzero, do NOT
    restart the service. Operator must reconcile manually.
- Env-file-present-with-empty-`HBV_API_KEY` is also refused, not silently
  synthesized.
- New `tests/test_installer_migration.py` covers all five branches plus a
  parameterized `TestActiveKeyInvariant` that runs each successful branch
  and asserts `ENV_FILE`'s `HBV_API_KEY` always equals `KEY_FILE`'s
  contents. **10 tests, all passing.**

**R-02 — Anonymous dashboard shell closed (Low).**

The interactive dashboard at `GET /` is now DISABLED by default. Under
production defaults, the collector's only anonymous public route is
`/health` — matching the reviewer's health-only public-route target
exactly.

- New env var `HBV_ENABLE_DASHBOARD=false` (default).
- When enabled, `/register_dashboard()` wires the route back on and the
  shell behaves as it did in v1.1.2 (paste-key-into-page, no secret in
  HTML). Documentation now says explicitly that even when enabled it
  belongs behind reverse-proxy access control (IP allowlist, mTLS,
  management VLAN).
- `TestPublicRouteInventory.test_only_health_is_anonymous_by_default` now
  asserts the strict health-only allowlist — no per-route exceptions.
- `test_dashboard_shell_disabled_by_default` asserts `GET /` returns 404
  under production defaults; `test_dashboard_shell_when_enabled_ships_no_secret`
  asserts the opt-in path still holds the "no secret in HTML" invariant.

**R-01 — Executable PowerShell coverage (Moderate).**

The v1.1.2 test suite covered the Windows agent's transport policy through
source-string assertions only. v1.1.3 adds a real PowerShell 7 Pester 5 job
running on a `windows-latest` GitHub Actions runner:

- `tests/agent-windows.Tests.ps1` dot-sources `Sentinel-Agent-Windows.ps1`
  and exercises `Test-TransportPolicy` and `Send-BeaconHTTP` directly.
- `Invoke-RestMethod` is mocked so no network I/O happens.
- Tests prove: (a) default endpoint is HTTPS; (b) `AllowInsecure` defaults
  to `$false`; (c) `Test-TransportPolicy` refuses `http://` unless
  `HBV_ALLOW_INSECURE=true`; (d) `Send-BeaconHTTP` short-circuits BEFORE
  `Invoke-RestMethod` on a refused endpoint; (e) TLS verification is
  default-on (no `-SkipCertificateCheck` unless
  `HBV_TLS_CA_BUNDLE=false`); (f) explicit lab overrides work end-to-end.
- CI workflow (`.github/workflows/ci.yml`) now runs two matrix jobs:
  `test-python` (Ubuntu, Python 3.10/3.11/3.12) and `test-powershell`
  (Windows). Both jobs use SHA-pinned actions; the Pester job uploads the
  NUnit XML as an artifact.

**Related latent-bug fix.**

The v1.1.2 Windows agent had `param(...)` positioned at line 575 of the
file — after the CONFIG hashtable and every function definition. Since
`param()` must be the first executable statement in a PowerShell script,
none of the `-Install / -Uninstall / -Test / -RunAgent` switches were
actually bound; every CLI invocation fell through to the usage banner.
Moved to the top of the file where it belongs. A test-friendly guard
(`if ($MyInvocation.InvocationName -eq '.') { return }`) means dot-sourcing
from the Pester suite doesn't accidentally launch the agent.

**Total test count: 57 Python tests + a full Pester suite.** Locally, on
Python 3.11 with `pip install fastapi 'pydantic>=2.5' httpx pytest`:
`57 passed in 3.25s`.

### A-01 — Retained accepted limitation

Same posture as v1.1.2: the collector's API key is a single shared secret
across every agent and reader; any holder can submit under any `agent_id`;
`ReplayGuard` state is in-memory per-process. Full rationale and
operational implications in the v1.1.2 section below. **Do not run the
collector under multi-worker uvicorn.**

### Upgrade impact (v1.1.2 → v1.1.3)

- Behavior change (opt-in required): `GET /` returns 404 by default. If
  your operators bookmarked the collector's dashboard URL and expect it to
  render, either set `HBV_ENABLE_DASHBOARD=true` in `collector.env` (and
  place the dashboard behind reverse-proxy access control), or use the API
  directly via `curl`/Prometheus/your monitoring stack.
- Installer change (transparent to running services): the collector env
  file's `HBV_API_KEY` is now authoritative. On a reinstall, if
  `collector.env` and `api.key` already agree, nothing changes. If they
  disagree, the installer STOPS and does not restart the service. This is
  the correct behaviour — the fact that this could happen previously was
  the H-01 defect.
- New env vars: `HBV_ENABLE_DASHBOARD` (default `false`).

## v1.1.2 hardening summary (follow-up review remediation)

v1.1.2 closed the three P0 findings the follow-up technical security
review of v1.1.1 flagged as blocking the hardened-production label (S-01,
S-02, S-03), landed the low-hanging Low finding (S-06), and expanded the
test suite to cover the boundaries the review called out (S-05). S-04 was
retained as a documented limitation.

### What changed in v1.1.2

**S-01 — Windows agent transport policy (High).**
The PowerShell agent mirrors the Linux HTTPS-first policy exactly:

- Default `HBV_COLLECTOR_URL` starts with `https://`.
- `Test-TransportPolicy` refuses any `http://` endpoint unless
  `HBV_ALLOW_INSECURE=true` is set — matches the Linux gate in intent.
- `HBV_TLS_CA_BUNDLE=false` enables `-SkipCertificateCheck` on
  `Invoke-RestMethod` (lab only, logs a warning). Any other value keeps
  PowerShell 7's default Windows trust-store verification. For a private
  CA in production, import the CA into "Local Machine → Trusted Root
  Certification Authorities" and leave `HBV_TLS_CA_BUNDLE` unset.

**S-02 — Installer and operator documentation (High).**

- `install-collector.sh` generates a stable API key on first install and
  writes hardened defaults into `collector.env`. It no longer emits the
  LAN dashboard URL or the unauthenticated `curl` example that used to
  contradict the runtime.
- `README.md`, `DEPLOYMENT-GUIDE.md`, and `INSTALLATION-CHECKLIST.md` all
  rewritten around a single production architecture: TLS reverse proxy in
  front of a loopback-bound collector, shared API key generated at
  install, agents on HTTPS.

**S-03 — Beacon ingestion honesty (Moderate → P0).**

- `receive_beacon` checks `db.store_beacon`'s return value and returns
  HTTP 503 (not a phantom 200) when the write fails.
- `ReplayGuard.rollback(agent_id, timestamp)` clears the reservation on
  failure so honest retries succeed once storage recovers.
- The "beacon received" log line is only emitted after successful commit.

**S-05 — Test-suite gaps closed (Moderate).** Nine new real-app tests on
top of the 37 that shipped with v1.1.1: rate-limit 429, DB-failure
round-trip, public-route inventory, docs-routes-off + opt-in, Windows
policy assertions (4).

**S-06 — Attack-surface reduction (Low).** `/docs`, `/redoc`, and
`/openapi.json` DISABLED by default; `HBV_ENABLE_DOCS=true` opts in for
dev.

### S-04 — Retained accepted limitation

The collector's API key is currently a single shared secret across every
agent and every reader. Any holder can submit beacons under any
`agent_id`, and the same credential unlocks reads. Beacon bodies are not
signed. Operational implications while S-04 is open:

- Treat `HBV_API_KEY` as a shared secret. Rotate it (see README §Key
  rotation) whenever any host is decommissioned or suspected compromised.
- Do NOT run the collector under a multi-worker uvicorn configuration.
  The in-memory `ReplayGuard` cannot coordinate across worker processes.
  `install-collector.sh` runs a single-worker uvicorn.
- If the collector runs behind a reverse proxy, rate limiting is per
  client IP as seen by the ASGI app. Behind a proxy that hides the real
  source IP, either terminate the proxy on the same box or add
  proxy-aware handling before scaling load.

Per-agent identity + persistent replay coordination + multi-worker
support is the next milestone (v1.2).

## v1.1.1 hardening (recap)

- Authentication REQUIRED by default and enforced on every telemetry
  endpoint.
- Collector binds to loopback by default; non-loopback binds warn at
  startup.
- CORS starts empty; wildcard force-disables `allow_credentials`.
- Beacon freshness window and duplicate rejection (`HBV_BEACON_MAX_SKEW`).
- Ephemeral-key warning if `HBV_API_KEY` isn't set.
- Dashboard shell contains no secret in HTML.
- Linux agent HTTPS-first with `HBV_ALLOW_INSECURE` opt-out and
  `HBV_TLS_CA_BUNDLE` for private CAs.
- SHA-pinned CI on Python 3.10/3.11/3.12.

## Reporting

Please open a private security advisory on the GitHub repository for any
new finding. Do not file a public issue.
