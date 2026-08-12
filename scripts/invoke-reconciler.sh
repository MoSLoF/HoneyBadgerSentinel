#!/bin/bash
# ═══════════════════════════════════════════════════════════════════════
# invoke-reconciler.sh
#
# Small, testable wrapper around scripts/reconcile-credentials.sh that
# correctly propagates the reconciler's exit code (review finding M-01).
#
# THE BUG THIS EXISTS TO PREVENT:
#   Earlier versions of install-collector.sh used:
#       if ! reconcile-credentials.sh; then
#           rc=$?
#           echo "..."
#           exit "$rc"
#       fi
#   `$?` inside the `if !` branch is the status of the *negated* command,
#   which is 0 whenever the reconciler failed. The wrapper printed the
#   refusal message and then exited 0 — automation saw "success" on a
#   refused install. The reviewer reproduced this and named it M-01.
#
# The correct pattern is to run the reconciler UN-negated and use else:
#   if reconciler; then :; else
#       rc=$?
#       ...
#       exit "$rc"
#   fi
# That branch's `$?` IS the reconciler's original nonzero status.
#
# Required env vars:
#   ENV_FILE   collector.env path
#   KEY_FILE   api.key path
#   TEMPLATE   collector.env.template path
#   RECONCILER path to reconcile-credentials.sh (overridable for tests)
#
# Exit codes:
#   0            reconciliation succeeded
#   whatever N   the reconciler returned nonzero — propagated verbatim
# ═══════════════════════════════════════════════════════════════════════

set -uo pipefail

: "${ENV_FILE:?ENV_FILE must be set}"
: "${KEY_FILE:?KEY_FILE must be set}"
: "${TEMPLATE:?TEMPLATE must be set}"
: "${RECONCILER:?RECONCILER must be set}"

if ENV_FILE="$ENV_FILE" KEY_FILE="$KEY_FILE" TEMPLATE="$TEMPLATE" \
        "$RECONCILER"; then
    exit 0
else
    # `$?` inside `else` IS the reconciler's original exit code (that's
    # what triggered entry into this branch). `$?` AFTER a bare `fi` is 0 —
    # that was the original M-01 defect. Do NOT restructure this without
    # re-running tests/test_installer_migration.py.
    rc=$?
    echo ""
    echo "[!] Credential reconciliation refused to proceed (exit=$rc)."
    echo "    NOT restarting the collector. Resolve the message above and re-run."
    exit "$rc"
fi
