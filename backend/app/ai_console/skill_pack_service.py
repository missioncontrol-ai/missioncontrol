"""SkillPackService — derives SkillPackManifest from an existing SkillSnapshot.

Classifies tarball entries by name/extension into MC's runtime-specific
instruction, tool-definition, and context buckets. Used by ConsoleGateway
to build the system_context string injected into new runtime sessions.
"""
from __future__ import annotations

import base64
import io
import json
import tarfile
from typing import Any

from app.ai_console.contracts import RuntimeKind, SkillPackManifest
from app.ai_console.runtime_config import configured_default_runtime

# File patterns that map to each manifest bucket.
_INSTRUCTION_NAMES = {"claude.md", "agents.md", "system_prompt.md", "system-prompt.md", "copilot-instructions.md"}
_TOOL_NAMES = {"tools.json"}
_TOOL_EXT = {".tool.json"}


def _classify(name: str) -> str:
    base = name.split("/")[-1].lower()
    if base in _INSTRUCTION_NAMES:
        return "instruction"
    if base in _TOOL_NAMES or any(base.endswith(ext) for ext in _TOOL_EXT):
        return "tool_definition"
    return "context"


def resolve_manifest(*, tarball_b64: str, snapshot_id: str, effective_version: str, sha256: str, runtime_kind: RuntimeKind | str) -> SkillPackManifest:
    """Derive a SkillPackManifest by scanning the tarball's entry names."""
    if isinstance(runtime_kind, str):
        try:
            runtime_kind = RuntimeKind(runtime_kind)
        except ValueError:
            runtime_kind = configured_default_runtime()

    instruction_files: list[str] = []
    tool_definition_files: list[str] = []
    context_files: list[str] = []

    if tarball_b64:
        try:
            raw = base64.b64decode(tarball_b64)
            with tarfile.open(fileobj=io.BytesIO(raw), mode="r:gz") as tf:
                for member in tf.getmembers():
                    bucket = _classify(member.name)
                    if bucket == "instruction":
                        instruction_files.append(member.name)
                    elif bucket == "tool_definition":
                        tool_definition_files.append(member.name)
                    else:
                        context_files.append(member.name)
        except Exception:
            pass  # Malformed tarball — return empty buckets

    return SkillPackManifest(
        snapshot_id=snapshot_id,
        format="mc-skill-pack/v1",
        runtime_kind=str(runtime_kind),
        instruction_files=sorted(instruction_files),
        tool_definition_files=sorted(tool_definition_files),
        context_files=sorted(context_files),
        sha256=sha256,
        effective_version=effective_version,
    )


def manifest_to_system_context(manifest: SkillPackManifest, tarball_b64: str) -> str:
    """Extract the text content of instruction files from the tarball."""
    if not tarball_b64 or not manifest.instruction_files:
        return ""
    try:
        raw = base64.b64decode(tarball_b64)
        with tarfile.open(fileobj=io.BytesIO(raw), mode="r:gz") as tf:
            parts: list[str] = []
            names = set(manifest.instruction_files)
            for member in tf.getmembers():
                if member.name not in names:
                    continue
                fobj = tf.extractfile(member)
                if fobj is None:
                    continue
                text = fobj.read().decode("utf-8", errors="replace")
                parts.append(f"# {member.name}\n{text}")
            return "\n\n".join(parts)
    except Exception:
        return ""
