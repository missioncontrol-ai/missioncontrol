from urllib.parse import urlparse

from fastapi import APIRouter, Request

router = APIRouter(tags=["onboarding"])


def _normalize_base_url(value: str) -> str:
    candidate = (value or "").strip()
    if not candidate:
        raise ValueError("endpoint cannot be empty")
    if "://" not in candidate:
        candidate = f"https://{candidate}"
    parsed = urlparse(candidate)
    if not parsed.scheme or not parsed.netloc:
        raise ValueError("endpoint must be a valid URL or hostname")
    return f"{parsed.scheme}://{parsed.netloc}".rstrip("/")


def _dedupe(values: list[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for value in values:
        if value in seen:
            continue
        seen.add(value)
        out.append(value)
    return out


def build_agent_onboarding_manifest(base_url: str) -> dict:
    integration_contract_version = "1.1.0"
    resolved_base_url = _normalize_base_url(base_url)
    default_base_urls = _dedupe(
        [
            resolved_base_url,
            "https://missioncontrol.internal.example",
            "http://localhost:8008",
        ]
    )
    mcp_env = {
        "MC_BASE_URL": resolved_base_url,
        "MC_TOKEN": "${MC_TOKEN}",
    }
    # mc serve env: only needs MC_BASE_URL; token is read from ~/.missioncontrol/session.json
    mc_serve_env = {
        "MC_BASE_URL": resolved_base_url,
    }
    return {
        "name": "MissionControl Agent Onboarding",
        "version": "1.0",
        "integration_contract_version": integration_contract_version,
        "generated_for_base_url": resolved_base_url,
        "endpoints": {
            "health": f"{resolved_base_url}/",
            "openapi": f"{resolved_base_url}/api/openapi.json",
            "explorer_tree": f"{resolved_base_url}/explorer/tree",
            "governance_active": f"{resolved_base_url}/governance/policy/active",
            "mcp_tools": f"{resolved_base_url}/mcp/tools",
            "mcp_call": f"{resolved_base_url}/mcp/call",
            "mcp_health": f"{resolved_base_url}/mcp/health",
            "skills_snapshot_resolve": f"{resolved_base_url}/skills/snapshots/resolve",
            "skills_sync_status": f"{resolved_base_url}/skills/sync/status",
            "ui": f"{resolved_base_url}/ui/",
        },
        "mcp_defaults": {
            "startup_timeout_sec": 45,
            "tool_timeout_sec": 60,
            "protocol_version": "2024-11-05",
            "healthcheck_path": "/",
            "endpoint_candidates": default_base_urls,
        },
        "mcp_server": {
            "name": "missioncontrol",
            "command": "mc",
            "args": ["serve"],
            "env": mcp_env,
        },
        # mc serve: the Rust-native MCP server — single binary, no Python required.
        # MC_TOKEN is intentionally omitted; mc reads the session from disk.
        # Run `mc login` once before using this entry.
        "mc_serve_mcp_server": {
            "name": "missioncontrol",
            "command": "mc",
            "args": ["serve"],
            "env": mc_serve_env,
        },
        "agent_configs": {
            "claude_code": {
                "missioncontrol": {
                    "command": "mc",
                    "args": ["serve"],
                    "env": mc_serve_env,
                },
            },
            "codex": {
                "missioncontrol": {
                    "command": "mc",
                    "args": ["serve"],
                    "env": mc_serve_env,
                },
            },
            "openclaw_nanoclaw": {
                "missioncontrol": {
                    "command": "mc",
                    "args": ["serve"],
                    "env": mc_serve_env,
                },
            },
            "gemini": {
                "missioncontrol": {
                    "command": "mc",
                    "args": ["serve"],
                    "env": mc_serve_env,
                },
            },
        },
        "bootstrap": {
            "remote_script": "bash <(curl -fsSL https://raw.githubusercontent.com/missioncontrol-ai/mc-integration/main/install.sh) --endpoint "
            + resolved_base_url
            + " --token ${MC_TOKEN} --agent both",
            "local_script": "bash install.sh --endpoint "
            + resolved_base_url
            + " --token ${MC_TOKEN} --agent both",
        },
        "automation": {
            "config_generator_script": "git clone https://github.com/missioncontrol-ai/mc-integration.git && cd mc-integration && bash install.sh --endpoint "
            + resolved_base_url
            + " --token ${MC_TOKEN} --agent both"
        },
        "notes": [
            "Run `mc login` once to authenticate; mc serve reads the session token from disk.",
            "All agents now use `mc serve` (Rust-native MCP server) — no Python missioncontrol-mcp required.",
            "Set the activation endpoint to your MissionControl instance before copying configs.",
            "Public distribution repo: https://github.com/missioncontrol-ai/mc-integration",
            "Use missioncontrol-explorer for inline terminal tree views.",
            "`mc daemon` is optional and only needed for event streaming / Matrix integration.",
        ],
    }


@router.get("/agent-onboarding.json")
def agent_onboarding_manifest(request: Request, endpoint: str | None = None):
    requested = (endpoint or "").strip() or str(request.base_url).rstrip("/")
    return build_agent_onboarding_manifest(requested)
