from __future__ import annotations

import os
import json
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol
from urllib.parse import parse_qs, urlparse


class SecretsProvider(Protocol):
    def get(self, name: str) -> str | None: ...

    def set(self, name: str, value: str) -> None: ...


@dataclass
class EnvSecretsProvider:
    def get(self, name: str) -> str | None:
        value = os.getenv(name)
        if value is None:
            return None
        trimmed = str(value).strip()
        return trimmed if trimmed else None

    def set(self, name: str, value: str) -> None:
        os.environ[name] = value


@dataclass
class InfisicalSecretsProvider:
    project_id: str | None
    environment: str | None
    path: str | None
    cli_bin: str = "infisical"

    def get(self, name: str) -> str | None:
        cmd = [self.cli_bin, "secrets", "get", name, "--plain"]
        if self.project_id:
            cmd.extend(["--projectId", self.project_id])
        if self.environment:
            cmd.extend(["--env", self.environment])
        if self.path:
            cmd.extend(["--path", self.path])
        try:
            proc = subprocess.run(cmd, check=False, capture_output=True, text=True, timeout=10)
        except Exception:
            return None
        if proc.returncode != 0:
            return None
        value = (proc.stdout or "").strip()
        return value or None

    def set(self, name: str, value: str) -> None:
        cmd = [self.cli_bin, "secrets", "set", name, value]
        if self.project_id:
            cmd.extend(["--projectId", self.project_id])
        if self.environment:
            cmd.extend(["--env", self.environment])
        if self.path:
            cmd.extend(["--path", self.path])
        subprocess.run(cmd, check=False, capture_output=True, text=True, timeout=15)


def _provider_name() -> str:
    raw = (os.getenv("MC_SECRETS_PROVIDER") or "env").strip().lower()
    if raw in {"env", "infisical"}:
        return raw
    return "env"


def build_provider() -> SecretsProvider:
    provider = _provider_name()
    if provider == "infisical":
        return InfisicalSecretsProvider(
            project_id=(os.getenv("MC_SECRETS_INFISICAL_PROJECT_ID") or "").strip() or None,
            environment=(os.getenv("MC_SECRETS_INFISICAL_ENV") or "").strip() or None,
            path=(os.getenv("MC_SECRETS_INFISICAL_PATH") or "").strip() or None,
            cli_bin=(os.getenv("MC_SECRETS_INFISICAL_CLI_BIN") or "infisical").strip() or "infisical",
        )
    return EnvSecretsProvider()


def resolve_secret_ref(value: str) -> str | None:
    if not value.startswith("secret://"):
        trimmed = value.strip()
        return trimmed or None
    parsed = urlparse(value)
    provider = (parsed.netloc or "").strip().lower()
    path = parsed.path.strip("/")
    query = parse_qs(parsed.query, keep_blank_values=False)
    if provider == "env":
        return EnvSecretsProvider().get(path)
    if provider == "infisical":
        infisical = InfisicalSecretsProvider(
            project_id=_first(query, "projectId") or (os.getenv("MC_SECRETS_INFISICAL_PROJECT_ID") or "").strip() or None,
            environment=_first(query, "env") or (os.getenv("MC_SECRETS_INFISICAL_ENV") or "").strip() or None,
            path=_first(query, "path") or (os.getenv("MC_SECRETS_INFISICAL_PATH") or "").strip() or None,
            cli_bin=(os.getenv("MC_SECRETS_INFISICAL_CLI_BIN") or "infisical").strip() or "infisical",
        )
        return infisical.get(path)
    return None


def get_secret(name: str, default: str | None = None) -> str | None:
    raw = os.getenv(name)
    if raw is not None:
        resolved = resolve_secret_ref(raw)
        if resolved is not None:
            return resolved
    profile_ref = _profile_secret_ref(name)
    if profile_ref:
        resolved_profile = resolve_secret_ref(profile_ref)
        if resolved_profile is not None:
            return resolved_profile
    value = build_provider().get(name)
    if value is not None:
        return value
    return default


def _first(query: dict[str, list[str]], key: str) -> str | None:
    values = query.get(key) or []
    if not values:
        return None
    candidate = (values[0] or "").strip()
    return candidate or None


def _profile_secret_ref(name: str) -> str | None:
    profile_name = (os.getenv("MC_SECRETS_PROFILE") or os.getenv("MC_AGENT_PROFILE") or "default").strip() or "default"
    mc_home = Path((os.getenv("MC_HOME") or "~/.missioncontrol")).expanduser()
    path = mc_home / "profiles" / profile_name / "secrets.json"
    try:
        content = path.read_text(encoding="utf-8")
        parsed = json.loads(content)
    except Exception:
        return None
    refs = parsed.get("refs") if isinstance(parsed, dict) else None
    if not isinstance(refs, dict):
        return None
    raw = refs.get(name)
    if raw is None:
        return None
    value = str(raw).strip()
    return value or None


def secrets_status() -> dict:
    profile_name = (os.getenv("MC_SECRETS_PROFILE") or os.getenv("MC_AGENT_PROFILE") or "default").strip() or "default"
    mc_home = Path((os.getenv("MC_HOME") or "~/.missioncontrol")).expanduser()
    profile_path = mc_home / "profiles" / profile_name / "secrets.json"
    provider = _provider_name()

    refs_count = 0
    provider_from_profile = None
    infisical = {}
    if profile_path.exists():
        try:
            parsed = json.loads(profile_path.read_text(encoding="utf-8"))
            refs = parsed.get("refs") if isinstance(parsed, dict) else None
            refs_count = len(refs) if isinstance(refs, dict) else 0
            profile_provider = parsed.get("provider") if isinstance(parsed, dict) else None
            if isinstance(profile_provider, str) and profile_provider.strip():
                provider_from_profile = profile_provider.strip().lower()
            infisical = {
                "project_id": parsed.get("infisical_project_id"),
                "env": parsed.get("infisical_env"),
                "path": parsed.get("infisical_path"),
            } if isinstance(parsed, dict) else {}
        except Exception:
            pass

    return {
        "provider_env": provider,
        "provider_profile": provider_from_profile,
        "effective_profile": profile_name,
        "profile_path": str(profile_path),
        "profile_exists": profile_path.exists(),
        "refs_count": refs_count,
        "infisical": infisical,
    }
