"""Goose-backed evolve runner.

Builds a Goose recipe JSON from an evolve spec, runs goose headlessly,
parses stream-json output to extract a completion score, and returns
(recipe_path, score).
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import re
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

_LITELLM_HOST = os.getenv("MC_LITELLM_HOST", "http://litellm:4000")
_GOOSE_BIN = os.getenv("GOOSE_BIN", "goose")
_MAX_TURNS = int(os.getenv("GOOSE_EVOLVE_MAX_TURNS", "50"))
_GOOSE_MODEL = os.getenv("MC_GOOSE_MODEL", "local-agent")
_TIMEOUT_SECS = int(os.getenv("GOOSE_EVOLVE_TIMEOUT", "1800"))

# Allowed base dir for run_dir — prevents path traversal in multi-tenant use
_ALLOWED_EVOLVE_BASE = os.getenv("MC_GOOSE_EVOLVE_BASE", "/tmp/mc-evolve")

# Per-field length limits for spec validation
_MAX_NAME_LEN = 256
_MAX_DESC_LEN = 10_000
_MAX_TASKS = 50
_MAX_TASK_DESC_LEN = 4_000


class EvolveSpecError(ValueError):
    """Raised when an evolve spec fails validation."""


def validate_spec(spec: dict) -> None:
    """Raise EvolveSpecError if the spec is malformed or exceeds size limits."""
    if not isinstance(spec, dict):
        raise EvolveSpecError("spec must be a dict")

    name = spec.get("name", "")
    if not isinstance(name, str) or len(name) > _MAX_NAME_LEN:
        raise EvolveSpecError(f"spec.name must be a string ≤ {_MAX_NAME_LEN} chars")

    desc = spec.get("description", "")
    if not isinstance(desc, str) or len(desc) > _MAX_DESC_LEN:
        raise EvolveSpecError(f"spec.description must be a string ≤ {_MAX_DESC_LEN} chars")

    tasks = spec.get("tasks", [])
    if not isinstance(tasks, list):
        raise EvolveSpecError("spec.tasks must be a list")
    if len(tasks) > _MAX_TASKS:
        raise EvolveSpecError(f"spec.tasks may not exceed {_MAX_TASKS} entries")
    for i, t in enumerate(tasks):
        if not isinstance(t, dict):
            raise EvolveSpecError(f"spec.tasks[{i}] must be a dict")
        tdesc = t.get("description", "")
        if not isinstance(tdesc, str) or len(tdesc) > _MAX_TASK_DESC_LEN:
            raise EvolveSpecError(
                f"spec.tasks[{i}].description must be a string ≤ {_MAX_TASK_DESC_LEN} chars"
            )


def _safe_run_dir(run_dir: str) -> str:
    """Resolve run_dir and ensure it stays within the allowed base."""
    real = os.path.realpath(run_dir)
    allowed = os.path.realpath(_ALLOWED_EVOLVE_BASE)
    if not real.startswith(allowed + os.sep) and real != allowed:
        raise EvolveSpecError(
            f"run_dir '{run_dir}' resolves outside allowed base '{_ALLOWED_EVOLVE_BASE}'"
        )
    return real


def build_goose_recipe(spec: dict) -> dict:
    """Convert an evolve spec dict into a Goose recipe dict (JSON-serialisable)."""
    name = spec.get("name", "Evolve Mission")
    description = spec.get("description", "")
    tasks = spec.get("tasks", [])
    scoring = spec.get("scoring_criteria")

    task_lines = "\n".join(
        f"- {t.get('title', t.get('id', ''))}: {t.get('description', '')}"
        for t in tasks
    )
    scoring_block = (
        json.dumps(scoring, indent=2) if scoring else "Not specified."
    )

    # `prompt` is the headless user message that starts the session (required
    # for --no-session mode — recipes without `prompt` abort immediately).
    prompt = (
        f"You are an evolve-loop agent for mission '{name}'.\n\n"
        f"{description}\n\n"
        f"## Tasks\n{task_lines or 'No tasks specified.'}\n\n"
        f"## Scoring Criteria\n{scoring_block}\n\n"
        "Work through each task systematically. When you have finished, "
        "output a JSON object on its own line exactly like: "
        '{"score": <float 0.0-1.0>}'
    )

    return {
        "version": "1.0.0",
        "title": name,
        "description": description,
        "prompt": prompt,
    }


async def run_goose_evolve(
    mission_id: str,
    spec: dict,
    run_dir: str,
) -> tuple[str, Optional[float]]:
    """Run Goose against an evolve spec.

    Validates the spec, writes a recipe JSON to run_dir, executes goose
    headlessly with a hard timeout, parses stream-json for a score, and
    returns (recipe_path, score).  score is 1.0 on success without an
    explicit score line, 0.0 on failure.
    """
    validate_spec(spec)
    safe_dir = _safe_run_dir(run_dir)
    os.makedirs(safe_dir, exist_ok=True)
    recipe_path = os.path.join(safe_dir, f"{mission_id}.json")

    recipe = build_goose_recipe(spec)
    with open(recipe_path, "w") as fh:
        json.dump(recipe, fh, indent=2)
    logger.info("goose_evolve_recipe path=%s size=%d", recipe_path, os.path.getsize(recipe_path))

    api_key = os.getenv("MC_LITELLM_API_KEY") or os.getenv("LITELLM_API_KEY", "sk-evolve")

    env = {
        **os.environ,
        "GOOSE_PROVIDER": "litellm",
        "LITELLM_HOST": _LITELLM_HOST,
        "LITELLM_API_KEY": api_key,
        "GOOSE_MODEL": _GOOSE_MODEL,
        "GOOSE_MODE": "Auto",
        "XDG_CONFIG_HOME": safe_dir,
        "HOME": safe_dir,
    }

    cmd = [
        _GOOSE_BIN,
        "run",
        "--recipe", recipe_path,
        "--quiet",
        "--output-format", "stream-json",
        "--no-session",
        "--max-turns", str(_MAX_TURNS),
    ]

    logger.info("goose_evolve_start mission_id=%s timeout=%ds", mission_id, _TIMEOUT_SECS)

    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        env=env,
        cwd=safe_dir,
    )

    async def _drain_stdout() -> Optional[float]:
        found: Optional[float] = None
        assert proc.stdout is not None
        async for raw in proc.stdout:
            line = raw.decode("utf-8", errors="replace").strip()
            if not line:
                continue
            extracted = _extract_score(line)
            if extracted is not None:
                found = extracted
        return found

    async def _drain_stderr() -> bytes:
        assert proc.stderr is not None
        return await proc.stderr.read()

    timed_out = False
    try:
        score, stderr_bytes = await asyncio.wait_for(
            asyncio.gather(_drain_stdout(), _drain_stderr()),
            timeout=_TIMEOUT_SECS,
        )
    except asyncio.TimeoutError:
        timed_out = True
        logger.error(
            "goose_evolve_timeout mission_id=%s after %ds — killing process",
            mission_id,
            _TIMEOUT_SECS,
        )
        try:
            proc.kill()
        except ProcessLookupError:
            pass
        score, stderr_bytes = None, b"<timeout>"

    await proc.wait()

    if timed_out:
        return recipe_path, 0.0

    if proc.returncode != 0:
        stderr_text = stderr_bytes.decode("utf-8", errors="replace")
        logger.error(
            "goose_evolve_failed mission_id=%s rc=%d stderr=%s",
            mission_id,
            proc.returncode,
            stderr_text[:500],
        )
        if score is None:
            score = 0.0
    else:
        if score is None:
            logger.warning(
                "goose_evolve_no_score mission_id=%s — no score line emitted, defaulting to 1.0",
                mission_id,
            )
            score = 1.0
        logger.info("goose_evolve_complete mission_id=%s score=%s", mission_id, score)

    return recipe_path, score


def _extract_score(line: str) -> Optional[float]:
    """Parse a stream-json line for a score value emitted by the agent.

    Actual stream-json format from goose:
      {"type": "message", "message": {"content": [{"type": "text", "text": "..."}], ...}}
    """
    try:
        event = json.loads(line)
    except json.JSONDecodeError:
        return None

    if event.get("type") != "message":
        return None

    # Content is nested inside event["message"]["content"]
    for block in event.get("message", {}).get("content", []):
        if isinstance(block, dict) and block.get("type") == "text":
            found = _find_score_in_text(block.get("text", ""))
            if found is not None:
                return found

    return None


def _find_score_in_text(text: str) -> Optional[float]:
    """Extract a float score from agent-emitted text.

    Accepts:
    - {"score": 0.85}        — canonical form
    - ```json\\n{"score": 0.85}\\n```  — markdown-fenced
    - 'score': 0.85          — single-quoted keys
    - {"score": 85}          — percent-style (normalised to 0.85)
    """
    # Strip markdown code fences so the regex sees bare JSON
    cleaned = re.sub(r"```[a-z]*\n?", "", text)

    # Match: optional quote/brace before "score", optional quote/brace after,
    # then colon and a number.  Works for {"score": X}, 'score': X, score: X.
    m = re.search(r"""["'{]?\bscore\b["'}]?\s*:\s*([0-9]*\.?[0-9]+)""", cleaned)
    if m:
        try:
            v = float(m.group(1))
            if 0.0 <= v <= 1.0:
                return v
            # Percent-style: agent said e.g. 85 meaning 0.85. Only normalise
            # values >= 10 to avoid misinterpreting 1.5 (slightly-over-range).
            if 10.0 <= v <= 100.0:
                logger.debug("score appears to be percent-style (%s), normalising to %s", v, v / 100.0)
                return round(v / 100.0, 4)
        except ValueError:
            pass
    return None
