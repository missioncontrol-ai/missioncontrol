#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PREFIX="${MC_INSTALL_PREFIX:-$HOME/.local/bin}"
TARGET="${MC_INSTALL_TARGET:-$PREFIX/mc}"

try_download_release() {
  local os arch release_url
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

  release_url="https://github.com/missioncontrol-ai/missioncontrol/releases/latest/download/mc-${os}-${arch}"
  echo "trying binary download: $release_url"
  if curl -fsSL --max-time 10 -o "$TARGET.tmp" "$release_url" 2>/dev/null; then
    mv "$TARGET.tmp" "$TARGET"
    chmod +x "$TARGET"
    echo "installed mc from release binary"
    return 0
  fi
  rm -f "$TARGET.tmp"
  return 1
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
