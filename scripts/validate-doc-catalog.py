#!/usr/bin/env python3
"""Validate docs/catalog/ structure and referenced paths.

Exits non-zero if any structural violation is found.
Run: python scripts/validate-doc-catalog.py
"""
import sys
import yaml
from pathlib import Path

ROOT = Path(__file__).parent.parent
CATALOG = ROOT / "docs" / "catalog"
INDEX = CATALOG / "index.yaml"
SCHEMA = CATALOG / "schema.yaml"

REQUIRED_DOMAIN_FIELDS = [
    "id", "title", "status", "maturity", "summary",
    "source_of_truth", "entrypoints", "implemented",
    "gaps", "todos", "last_reviewed", "confidence",
]
VALID_STATUS = {"planned", "stub", "partial", "active", "deprecated", "experimental"}
VALID_MATURITY = {"experimental", "alpha", "beta", "productionish"}
VALID_CONFIDENCE = {"low", "medium", "high"}

errors = []


def err(msg):
    errors.append(msg)
    print(f"ERROR: {msg}")


def check_path_exists(ref_path, context):
    full = ROOT / ref_path
    if not full.exists():
        err(f"{context}: referenced path does not exist: {ref_path}")


# Load index
if not INDEX.exists():
    err(f"Missing {INDEX}")
    sys.exit(1)

with open(INDEX) as f:
    index = yaml.safe_load(f)

# Check index read_first references
for p in index.get("read_first", []):
    check_path_exists(p, "index.read_first")

# Validate each domain
for domain in index.get("domains", []):
    domain_file = ROOT / domain["file"]
    if not domain_file.exists():
        err(f"Domain file missing: {domain['file']}")
        continue

    with open(domain_file) as f:
        data = yaml.safe_load(f)

    ctx = domain["file"]

    # Required fields
    for field in REQUIRED_DOMAIN_FIELDS:
        if field not in data:
            err(f"{ctx}: missing required field: {field}")

    # Enum validation
    if data.get("status") not in VALID_STATUS:
        err(f"{ctx}: invalid status '{data.get('status')}' — must be one of {VALID_STATUS}")
    if data.get("maturity") not in VALID_MATURITY:
        err(f"{ctx}: invalid maturity '{data.get('maturity')}' — must be one of {VALID_MATURITY}")
    if data.get("confidence") not in VALID_CONFIDENCE:
        err(f"{ctx}: invalid confidence '{data.get('confidence')}' — must be one of {VALID_CONFIDENCE}")

    # Verify source_of_truth paths
    sot = data.get("source_of_truth", {})
    for doc_path in sot.get("docs", []):
        check_path_exists(doc_path, f"{ctx}.source_of_truth.docs")
    for code_path in sot.get("code_paths", []):
        check_path_exists(code_path, f"{ctx}.source_of_truth.code_paths")

    # Verify read_next references
    for p in data.get("read_next", []):
        check_path_exists(p, f"{ctx}.read_next")

if errors:
    print(f"\n{len(errors)} error(s) found. Fix before merging.")
    sys.exit(1)
else:
    print(f"Catalog OK — {len(index['domains'])} domains validated.")
