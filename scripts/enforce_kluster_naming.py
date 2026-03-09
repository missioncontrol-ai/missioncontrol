#!/usr/bin/env python3
"""Enforce MissionControl naming convention: prefer `kluster` over `cluster`.

This check uses a committed baseline allowlist so existing legacy references can
remain temporarily, while any newly introduced `cluster` references fail CI.
"""

from __future__ import annotations

import argparse
import pathlib
import re
import subprocess
import sys


WORD_RE = re.compile(r"\bcluster(s)?\b")
ALLOWLIST_PATH = pathlib.Path(__file__).with_name("kluster_naming_allowlist.txt")


def repo_root() -> pathlib.Path:
    out = subprocess.check_output(["git", "rev-parse", "--show-toplevel"], text=True).strip()
    return pathlib.Path(out)


def tracked_files(root: pathlib.Path) -> list[pathlib.Path]:
    out = subprocess.check_output(["git", "ls-files"], cwd=root, text=True)
    files = []
    for rel in out.splitlines():
        if not rel:
            continue
        p = root / rel
        if p.is_file():
            files.append(p)
    return files


def should_skip(rel: str) -> bool:
    skip_prefixes = (
        ".git/",
        ".venv/",
        "node_modules/",
    )
    if rel.startswith(skip_prefixes):
        return True
    if rel == str(ALLOWLIST_PATH.relative_to(repo_root())):
        return True
    if rel == "scripts/enforce_kluster_naming.py":
        return True
    return False


def find_occurrences(root: pathlib.Path) -> list[str]:
    hits: list[str] = []
    for path in tracked_files(root):
        rel = path.relative_to(root).as_posix()
        if should_skip(rel):
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
        for i, line in enumerate(text.splitlines(), start=1):
            if WORD_RE.search(line):
                hits.append(f"{rel}:{i}:{line.strip()}")
    return hits


def load_allowlist() -> set[str]:
    if not ALLOWLIST_PATH.exists():
        return set()
    items: set[str] = set()
    for line in ALLOWLIST_PATH.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        items.add(line)
    return items


def write_allowlist(items: list[str]) -> None:
    ALLOWLIST_PATH.write_text(
        "# Legacy `cluster` references allowed temporarily.\n"
        "# Keep this file shrinking. New entries should be avoided.\n"
        + "\n".join(items)
        + "\n",
        encoding="utf-8",
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--write-allowlist", action="store_true")
    args = parser.parse_args()

    root = repo_root()
    hits = sorted(find_occurrences(root))

    if args.write_allowlist:
        write_allowlist(hits)
        print(f"Wrote {ALLOWLIST_PATH}")
        return 0

    allow = load_allowlist()
    violations = [hit for hit in hits if hit not in allow]
    stale = [item for item in sorted(allow) if item not in set(hits)]

    if violations:
        print("ERROR: New `cluster` naming references detected (use `kluster`).")
        for item in violations:
            print(f"  {item}")
        print("\nIf intentional, update baseline with:")
        print("  python scripts/enforce_kluster_naming.py --write-allowlist")
        return 1

    if stale:
        print("Note: allowlist contains stale entries you can remove by regenerating it.")
        for item in stale[:20]:
            print(f"  stale: {item}")
        if len(stale) > 20:
            print(f"  ... and {len(stale) - 20} more")

    print("Kluster naming check passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
