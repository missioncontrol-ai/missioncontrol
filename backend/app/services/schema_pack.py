from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from fastapi import HTTPException, Request


DEFAULT_SCHEMA_PACK = {
    "version": "v1",
    "name": "main",
    "description": "Default mission/kluster/task schema",
    "entities": {
        "mission": {
            "required": ["name"],
            "optional": ["description", "owners", "contributors", "tags", "visibility", "status"],
        },
        "kluster": {
            "required": ["name"],
            "optional": ["description", "owners", "contributors", "tags", "status", "mission_id"],
        },
        "task": {
            "required": ["kluster_id", "title"],
            "optional": [
                "description",
                "status",
                "owner",
                "contributors",
                "dependencies",
                "definition_of_done",
                "related_artifacts",
            ],
        },
        "doc": {
            "required": ["kluster_id", "title", "body"],
            "optional": ["doc_type", "status", "provenance"],
        },
        "artifact": {
            "required": ["kluster_id", "name", "uri"],
            "optional": ["artifact_type", "status", "provenance"],
        },
    },
}


def load_schema_pack() -> dict[str, Any]:
    path_env = (os.getenv("MC_SCHEMA_PACK_FILE") or "").strip()
    if path_env:
        path = Path(path_env)
    else:
        root = Path(__file__).resolve().parents[3]
        path = root / "docs" / "schema-packs" / "main.json"

    if not path.exists():
        return DEFAULT_SCHEMA_PACK

    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return DEFAULT_SCHEMA_PACK
    return _validate_schema_pack(payload)


def _validate_schema_pack(payload: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(payload, dict):
        return DEFAULT_SCHEMA_PACK
    version = payload.get("version")
    name = payload.get("name")
    entities = payload.get("entities")
    if not isinstance(version, str) or not version:
        return DEFAULT_SCHEMA_PACK
    if not isinstance(name, str) or not name:
        return DEFAULT_SCHEMA_PACK
    if not isinstance(entities, dict) or not entities:
        return DEFAULT_SCHEMA_PACK

    out = {
        "version": version,
        "name": name,
        "description": str(payload.get("description") or ""),
        "entities": {},
    }
    for entity_name, entity_spec in entities.items():
        if not isinstance(entity_name, str) or not isinstance(entity_spec, dict):
            continue
        required = entity_spec.get("required") or []
        optional = entity_spec.get("optional") or []
        if not isinstance(required, list) or not isinstance(optional, list):
            continue
        out["entities"][entity_name] = {
            "required": [str(x) for x in required],
            "optional": [str(x) for x in optional],
        }
    if not out["entities"]:
        return DEFAULT_SCHEMA_PACK
    return out


def enforce_schema_pack(
    *,
    request: Request,
    entity_type: str,
    payload: dict[str, Any],
    operation: str,
) -> dict[str, Any]:
    app_obj = getattr(request, "app", None)
    app_state = getattr(app_obj, "state", None)
    schema_pack = getattr(app_state, "schema_pack", DEFAULT_SCHEMA_PACK) or DEFAULT_SCHEMA_PACK
    entities = schema_pack.get("entities") or {}
    spec = entities.get(entity_type)
    if not isinstance(spec, dict):
        return payload

    required = set(spec.get("required") or [])
    optional = set(spec.get("optional") or [])
    allowed = required | optional
    payload_keys = set(payload.keys())
    unknown = sorted(payload_keys - allowed)

    if operation == "create":
        missing = sorted(k for k in required if k not in payload or _is_missing(payload.get(k)))
    else:
        missing = []

    if unknown or missing:
        raise HTTPException(
            status_code=422,
            detail={
                "code": "schema_pack_validation_failed",
                "entity_type": entity_type,
                "pack_name": str(schema_pack.get("name") or ""),
                "pack_version": str(schema_pack.get("version") or ""),
                "unknown_fields": unknown,
                "missing_required": missing,
            },
        )
    return payload


def _is_missing(value: Any) -> bool:
    if value is None:
        return True
    if isinstance(value, str):
        return not value.strip()
    return False
