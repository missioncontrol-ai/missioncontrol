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
import tempfile
from typing import Optional

logger = logging.getLogger(__name__)

_LITELLM_HOST = os.getenv("MC_LITELLM_HOST", "http://litellm:4000")
_GOOSE_BIN = os.getenv("GOOSE_BIN", "goose")
_MAX_TURNS = int(os.getenv("GOOSE_EVOLVE_MAX_TURNS", "50"))
_GOOSE_MODEL = os.getenv("MC_GOOSE_MODEL", "litellm/local-agent")


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

    # `instructions` becomes the additional system prompt; `prompt` is the
    # headless user message that actually starts the session (required for
    # headless/no-session mode — recipes without `prompt` abort immediately).
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

    Writes a recipe JSON to run_dir, executes goose headlessly, parses
    stream-json for a score, and returns (recipe_path, score).
    score is 1.0 on success without explicit score, 0.0 on failure.
    """
    os.makedirs(run_dir, exist_ok=True)
    recipe_path = os.path.join(run_dir, f"{mission_id}.json")

    recipe = build_goose_recipe(spec)
    with open(recipe_path, "w") as fh:
        json.dump(recipe, fh, indent=2)

    api_key = os.getenv("LITELLM_API_KEY", "sk-evolve")

    env = {
        **os.environ,
        "GOOSE_PROVIDER": "litellm",
        "LITELLM_HOST": _LITELLM_HOST,
        "LITELLM_API_KEY": api_key,
        "GOOSE_MODEL": _GOOSE_MODEL,
        "GOOSE_MODE": "Auto",
        "XDG_CONFIG_HOME": run_dir,
        "HOME": run_dir,
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

    logger.info("goose_evolve_start mission_id=%s", mission_id)

    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        env=env,
        cwd=run_dir,
    )

    score: Optional[float] = None

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

    score, stderr_bytes = await asyncio.gather(_drain_stdout(), _drain_stderr())
    await proc.wait()

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
    """Extract a float score from {"score": <float>} anywhere in text."""
    m = re.search(r'\{"score"\s*:\s*([0-9]*\.?[0-9]+)\}', text)
    if m:
        try:
            v = float(m.group(1))
            if 0.0 <= v <= 1.0:
                return v
        except ValueError:
            pass
    return None
