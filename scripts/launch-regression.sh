#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
MC_MANIFEST_PATH="${ROOT_DIR}/integrations/mc/Cargo.toml"

STRICT="${MC_LAUNCH_REQUIRE_ALL:-1}"
WORKDIR="$(mktemp -d)"
trap 'rm -rf "$WORKDIR"' EXIT

TEST_HOME="$WORKDIR/home"
TEST_MC_HOME="$WORKDIR/mc-home"
TEST_BIN="$WORKDIR/bin"
mkdir -p "$TEST_HOME" "$TEST_MC_HOME" "$TEST_BIN"

# Preserve rust toolchain resolution after overriding HOME for isolation.
ORIG_HOME="${HOME:-}"
export CARGO_HOME="${CARGO_HOME:-$ORIG_HOME/.cargo}"
export RUSTUP_HOME="${RUSTUP_HOME:-$ORIG_HOME/.rustup}"

export HOME="$TEST_HOME"
export MC_HOME="$TEST_MC_HOME"
export MC_BASE_URL="${MC_BASE_URL:-http://127.0.0.1:8008}"
export MC_TOKEN="${MC_TOKEN:-launch-regression-token}"

cat >"$TEST_BIN/codex" <<'EOF'
#!/usr/bin/env bash
exit 0
EOF
cat >"$TEST_BIN/claude" <<'EOF'
#!/usr/bin/env bash
exit 0
EOF
cat >"$TEST_BIN/gemini" <<'EOF'
#!/usr/bin/env bash
exit 0
EOF
cat >"$TEST_BIN/openclaw" <<'EOF'
#!/usr/bin/env bash
exit 0
EOF
cat >"$TEST_BIN/nanoclaw" <<'EOF'
#!/usr/bin/env bash
exit 0
EOF
chmod +x "$TEST_BIN/"*
export PATH="$TEST_BIN:$PATH"

run_launch() {
  local agent="$1"
  shift
  cargo run --quiet --manifest-path "$MC_MANIFEST_PATH" -- launch "$agent" --skip-config-gen "$@"
}

latest_instance_dir() {
  ls -1dt "$MC_HOME"/instances/* 2>/dev/null | head -n1
}

assert_exists() {
  local path="$1"
  [[ -e "$path" ]] || { echo "[launch-regression] missing expected path: $path" >&2; exit 1; }
}

assert_not_exists() {
  local path="$1"
  [[ ! -e "$path" ]] || { echo "[launch-regression] unexpected path exists: $path" >&2; exit 1; }
}

echo "[launch-regression] default instance-local config behavior"
run_launch codex
inst="$(latest_instance_dir)"
assert_exists "$inst/home/.codex/config.toml"
assert_not_exists "$HOME/.codex/config.toml"

run_launch claude
inst="$(latest_instance_dir)"
assert_exists "$inst/home/.claude.json"
assert_not_exists "$HOME/.claude.json"

run_launch gemini
inst="$(latest_instance_dir)"
assert_exists "$inst/home/.gemini/settings.json"
assert_not_exists "$HOME/.gemini/settings.json"

run_launch openclaw
inst="$(latest_instance_dir)"
assert_exists "$inst/mc/config/openclaw.acp.json"

run_launch nanoclaw
inst="$(latest_instance_dir)"
assert_exists "$inst/mc/config/nanoclaw.acp.json"

echo "[launch-regression] legacy global config escape hatch"
run_launch codex --legacy-global-config
assert_exists "$HOME/.codex/config.toml"

run_launch claude --legacy-global-config
assert_exists "$HOME/.claude.json"

run_launch gemini --legacy-global-config
assert_exists "$HOME/.gemini/settings.json"

if [[ "$STRICT" == "1" ]]; then
  echo "[launch-regression] strict mode: all agent checks required and passed"
fi

echo "[launch-regression] ok"
