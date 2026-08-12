#!/bin/bash
# ═══════════════════════════════════════════════════════════════════════
# reconcile-credentials.sh
#
# Reconcile the two credential authorities on the collector host:
#   ENV_FILE  — /etc/hbv-sentinel/collector.env  (HBV_API_KEY=...)
#   KEY_FILE  — /etc/hbv-sentinel/api.key        (mode 0600, one-line token)
#
# The problem this script exists to solve (review finding H-01):
#   install-collector.sh <= v1.1.2 would create KEY_FILE whenever it was
#   absent (fresh install AND v1.1.1-upgrade) but would only write ENV_FILE
#   when THAT was absent (fresh install only). On upgrade the two files
#   ended up with different values, the running collector kept the old key
#   from ENV_FILE, and the installer told operators/agents to use the new
#   KEY_FILE value — which was wrong.
#
# Contract (ENV_FILE is authoritative):
#   fresh:                  neither exists → generate a token; write BOTH.
#   upgrade / partial:      ENV_FILE has HBV_API_KEY, KEY_FILE missing
#                                          → populate KEY_FILE from ENV_FILE.
#   reinstall:              both exist and agree → no changes.
#   partial:                KEY_FILE exists, ENV_FILE missing
#                                          → write ENV_FILE from KEY_FILE.
#   MISMATCH:               both exist and disagree
#                                          → refuse; the operator must decide.
#   env-file-no-key:        ENV_FILE exists but HBV_API_KEY not set or empty
#                                          → treat like ENV_FILE missing for
#                                            the key line specifically; write
#                                            a merged file preserving the rest.
#
# Env inputs (must be set by the caller):
#   ENV_FILE   path to collector.env
#   KEY_FILE   path to api.key
#   TEMPLATE   path to a template collector.env used only on fresh install
#
# On mismatch, exits nonzero with a message; caller must not restart the
# service. Otherwise exits 0. Never restarts anything itself.
# ═══════════════════════════════════════════════════════════════════════

set -euo pipefail

: "${ENV_FILE:?ENV_FILE must be set}"
: "${KEY_FILE:?KEY_FILE must be set}"
: "${TEMPLATE:?TEMPLATE must be set (path to a fresh-install collector.env template)}"

read_env_key() {
    # Extract HBV_API_KEY=<value> from a shell-format env file. Handles the
    # simple installer-written form; does NOT try to be a full sh parser.
    # Empty stdout means "not set" or "set to empty string".
    if [ ! -f "$1" ]; then
        return 0
    fi
    grep -E '^[[:space:]]*HBV_API_KEY[[:space:]]*=' "$1" | tail -n1 \
        | sed -E 's/^[[:space:]]*HBV_API_KEY[[:space:]]*=[[:space:]]*//' \
        | sed -E 's/^"(.*)"$/\1/; s/^'\''(.*)'\''$/\1/'
}

write_key_file() {
    local value="$1" dest="$2"
    umask 077
    printf '%s\n' "$value" > "$dest"
    chmod 600 "$dest"
}

set_env_key() {
    # Ensure ENV_FILE contains exactly one uncommented HBV_API_KEY=<value>
    # line, replacing whatever was there. Preserves surrounding content.
    local value="$1" dest="$2"
    if grep -qE '^[[:space:]]*HBV_API_KEY[[:space:]]*=' "$dest"; then
        # Replace the first uncommented occurrence.
        awk -v val="$value" '
            BEGIN { done = 0 }
            /^[[:space:]]*HBV_API_KEY[[:space:]]*=/ && !done { print "HBV_API_KEY=" val; done = 1; next }
            { print }
        ' "$dest" > "$dest.tmp"
        mv "$dest.tmp" "$dest"
    else
        printf '\nHBV_API_KEY=%s\n' "$value" >> "$dest"
    fi
    chmod 600 "$dest"
}

env_exists=0
key_exists=0
[ -f "$ENV_FILE" ] && env_exists=1
[ -f "$KEY_FILE" ] && key_exists=1

env_key=""
key_file_key=""
if [ "$env_exists" -eq 1 ]; then
    env_key="$(read_env_key "$ENV_FILE" || true)"
fi
if [ "$key_exists" -eq 1 ]; then
    key_file_key="$(head -n1 "$KEY_FILE" | tr -d '\r\n')"
fi

# ── Fresh install: nothing on disk. Generate one canonical value. ─────
if [ "$env_exists" -eq 0 ] && [ "$key_exists" -eq 0 ]; then
    NEW_KEY="$(python3 -c 'import secrets; print(secrets.token_urlsafe(32))')"
    write_key_file "$NEW_KEY" "$KEY_FILE"
    # Materialize the env file from the template with the generated key.
    umask 077
    sed "s|__HBV_API_KEY__|$NEW_KEY|" "$TEMPLATE" > "$ENV_FILE"
    chmod 600 "$ENV_FILE"
    echo "[+] fresh install: generated new API key"
    exit 0
fi

# ── ENV_FILE holds the active key. Populate/repair KEY_FILE from it. ──
if [ "$env_exists" -eq 1 ] && [ "$key_exists" -eq 0 ]; then
    if [ -z "$env_key" ]; then
        echo "[!] $ENV_FILE exists but HBV_API_KEY is unset or empty."
        echo "    Refusing to synthesize a key here — edit $ENV_FILE to set"
        echo "    HBV_API_KEY explicitly, then re-run the installer."
        exit 2
    fi
    write_key_file "$env_key" "$KEY_FILE"
    echo "[+] upgrade: populated $KEY_FILE from existing $ENV_FILE"
    exit 0
fi

# ── KEY_FILE holds the value; ENV_FILE missing. Materialize env. ──────
if [ "$env_exists" -eq 0 ] && [ "$key_exists" -eq 1 ]; then
    if [ -z "$key_file_key" ]; then
        echo "[!] $KEY_FILE exists but is empty. Refusing to proceed."
        exit 2
    fi
    umask 077
    sed "s|__HBV_API_KEY__|$key_file_key|" "$TEMPLATE" > "$ENV_FILE"
    chmod 600 "$ENV_FILE"
    echo "[+] partial: wrote $ENV_FILE from existing $KEY_FILE"
    exit 0
fi

# ── Both exist. Compare. ──────────────────────────────────────────────
if [ "$env_key" = "$key_file_key" ] && [ -n "$env_key" ]; then
    echo "[=] reinstall: $ENV_FILE and $KEY_FILE agree; no changes"
    exit 0
fi

# Both exist but disagree — or one is empty and the other isn't. Refuse.
#
# CRITICAL: do NOT print either credential value here (review finding M-02).
# Installer stdout is captured by cloud-init, config-management logs, CI
# runners, and interactive terminals — all of which have broader read/retain
# boundaries than 0600 secret files. Print file paths, presence, length,
# and a short non-reversible fingerprint so the operator can tell which
# file matches what the running service is currently using without exposing
# the actual token.
fingerprint() {
    local v="$1"
    if [ -z "$v" ]; then
        echo "<empty>"
        return
    fi
    # SHA-256 truncated to 12 hex chars: enough to distinguish two known
    # values, nowhere near enough to reconstruct either.
    printf '%s' "$v" | sha256sum | cut -c1-12
}

echo "[!] MISMATCH between credential authorities:"
echo "    $ENV_FILE  fingerprint=$(fingerprint "$env_key") len=${#env_key}"
echo "    $KEY_FILE  fingerprint=$(fingerprint "$key_file_key") len=${#key_file_key}"
echo ""
echo "    Credential values are NOT printed (would leak to installer logs)."
echo "    The installer will NOT choose for you. Resolve by editing one file"
echo "    to match the other — usually collector.env is what the running"
echo "    service is currently using — then re-run the installer."
exit 3
