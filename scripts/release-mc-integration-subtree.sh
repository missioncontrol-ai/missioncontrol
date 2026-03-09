#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
EXPORT_PREFIX="${MC_INTEGRATION_EXPORT_PREFIX:-distribution/mc-integration}"
TARGET_REMOTE="${MC_INTEGRATION_REMOTE:-mc-integration}"
TARGET_BRANCH="${MC_INTEGRATION_BRANCH:-main}"
SPLIT_BRANCH="${MC_INTEGRATION_SPLIT_BRANCH:-tmp/mc-integration-subtree}"
DRY_RUN="${DRY_RUN:-0}"

cd "$ROOT_DIR"

if [[ ! -d "$EXPORT_PREFIX" ]]; then
  echo "Export prefix not found: $EXPORT_PREFIX" >&2
  exit 1
fi

if [[ -z "$(git ls-files "$EXPORT_PREFIX")" ]]; then
  echo "No tracked files under '$EXPORT_PREFIX'." >&2
  echo "Commit integration export files before running subtree release." >&2
  exit 1
fi

echo "[subtree] Splitting '$EXPORT_PREFIX' into '$SPLIT_BRANCH'"
git subtree split --prefix "$EXPORT_PREFIX" -b "$SPLIT_BRANCH" >/dev/null

echo "[subtree] Prepared split branch: $SPLIT_BRANCH"
git --no-pager log --oneline -n 3 "$SPLIT_BRANCH"

if [[ "$DRY_RUN" == "1" ]]; then
  echo "[subtree] DRY_RUN=1, skipping push"
  exit 0
fi

if ! git remote get-url "$TARGET_REMOTE" >/dev/null 2>&1; then
  echo "Git remote '$TARGET_REMOTE' is not configured." >&2
  echo "Example: git remote add $TARGET_REMOTE https://github.com/RyanMerlin/mc-integration.git" >&2
  exit 1
fi

echo "[subtree] Pushing to $TARGET_REMOTE/$TARGET_BRANCH"
git push "$TARGET_REMOTE" "$SPLIT_BRANCH:$TARGET_BRANCH"

echo "[subtree] Done"
