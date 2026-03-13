#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PREFIX="${MC_INSTALL_PREFIX:-$HOME/.local/bin}"
TARGET="${MC_INSTALL_TARGET:-$PREFIX/mc}"

if ! command -v cargo >/dev/null 2>&1; then
  echo "cargo is required to build mc" >&2
  exit 1
fi

echo "building mc (release)..."
(
  cd "$ROOT_DIR/integrations/mc"
  cargo build --release
)

mkdir -p "$PREFIX"
cp "$ROOT_DIR/integrations/mc/target/release/mc" "$TARGET"
chmod +x "$TARGET"

echo "installed mc to $TARGET"
if command -v mc >/dev/null 2>&1; then
  echo "mc on PATH: $(command -v mc)"
fi
"$TARGET" --version
