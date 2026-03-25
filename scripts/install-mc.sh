#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PREFIX="${MC_INSTALL_PREFIX:-$HOME/.local/bin}"
TARGET="${MC_INSTALL_TARGET:-$PREFIX/mc}"
ENV_FILE="${MC_ENV_FILE:-$ROOT_DIR/.env}"
AUTO_SHELL_HOOK="${MC_INSTALL_SHELL_HOOK:-0}"

append_shell_hook() {
  local rc_file="$1"
  local marker_begin="# >>> missioncontrol mc env >>>"
  local marker_end="# <<< missioncontrol mc env <<<"
  if [[ ! -f "$rc_file" ]]; then
    touch "$rc_file"
  fi
  if grep -Fq "$marker_begin" "$rc_file"; then
    echo "shell hook already present in $rc_file"
    return 0
  fi
  cat >>"$rc_file" <<EOF
$marker_begin
if [ -f "$ENV_FILE" ]; then
  set -a
  . "$ENV_FILE"
  set +a
fi
$marker_end
EOF
  echo "installed shell hook in $rc_file"
}

try_download_release() {
  local os arch base_url artifact
  os="$(uname -s | tr '[:upper:]' '[:lower:]')"
  arch="$(uname -m)"
  case "$arch" in
    x86_64)        arch="x86_64" ;;
    aarch64|arm64) arch="aarch64" ;;
    *) return 1 ;;
  esac
  case "$os" in
    linux|darwin) ;;
    *) return 1 ;;
  esac

  artifact="mc-${os}-${arch}"
  base_url="https://github.com/missioncontrol-ai/missioncontrol/releases/latest/download"

  echo "trying binary download: ${base_url}/${artifact}"
  if ! curl -fsSL --max-time 30 -o "$TARGET.tmp" "${base_url}/${artifact}" 2>/dev/null; then
    rm -f "$TARGET.tmp"
    return 1
  fi

  # Verify checksum
  if curl -fsSL --max-time 10 -o "$TARGET.checksums" "${base_url}/checksums.txt" 2>/dev/null; then
    local expected actual
    expected="$(grep " ${artifact}$" "$TARGET.checksums" | awk '{print $1}')"
    if [[ -n "$expected" ]]; then
      if command -v sha256sum >/dev/null 2>&1; then
        actual="$(sha256sum "$TARGET.tmp" | awk '{print $1}')"
      else
        actual="$(shasum -a 256 "$TARGET.tmp" | awk '{print $1}')"
      fi
      if [[ "$expected" != "$actual" ]]; then
        echo "checksum mismatch — aborting install" >&2
        rm -f "$TARGET.tmp" "$TARGET.checksums"
        return 1
      fi
      echo "checksum verified"
    else
      echo "warning: artifact not found in checksums.txt, skipping verification"
    fi
    rm -f "$TARGET.checksums"
  else
    echo "warning: could not download checksums.txt, skipping verification"
  fi

  mv "$TARGET.tmp" "$TARGET"
  chmod +x "$TARGET"
  echo "installed mc from release binary"
  return 0
}

mkdir -p "$PREFIX"

if ! try_download_release; then
  echo "binary download unavailable, building from source (requires cargo)..."
  if ! command -v cargo >/dev/null 2>&1; then
    echo "cargo is required to build mc — install Rust from https://rustup.rs" >&2
    exit 1
  fi
  (
    cd "$ROOT_DIR/integrations/mc"
    cargo build --release
  )
  cp "$ROOT_DIR/integrations/mc/target/release/mc" "$TARGET"
  chmod +x "$TARGET"
fi

echo "installed mc to $TARGET"
if command -v mc >/dev/null 2>&1; then
  echo "mc on PATH: $(command -v mc)"
fi
"$TARGET" --version

echo ""
echo "Launch an agent (auto-wires daemon + config):"
echo "  MC_TOKEN=<token> MC_BASE_URL=<url> mc launch claude"
echo "  MC_TOKEN=<token> MC_BASE_URL=<url> mc launch codex"
echo "  MC_TOKEN=<token> MC_BASE_URL=<url> mc launch gemini"

if [[ "$AUTO_SHELL_HOOK" == "1" ]]; then
  if [[ -f "$ENV_FILE" ]]; then
    append_shell_hook "$HOME/.zshrc"
    append_shell_hook "$HOME/.bashrc"
    echo "auto env loading enabled from $ENV_FILE"
  else
    echo "MC_INSTALL_SHELL_HOOK=1 set, but env file not found: $ENV_FILE" >&2
  fi
else
  echo ""
  echo "Optional: enable auto env loading into new shells"
  echo "  MC_INSTALL_SHELL_HOOK=1 MC_ENV_FILE=$ENV_FILE bash scripts/install-mc.sh"
fi
