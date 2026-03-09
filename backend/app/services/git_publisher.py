import json
import os
import subprocess
import tempfile
import hashlib
import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Optional
from urllib.parse import quote


class GitPublishError(RuntimeError):
    pass


@dataclass
class GitPublishConfig:
    enabled: bool
    repo_url: str
    branch: str
    base_path: str
    author_name: str
    author_email: str
    token: Optional[str]
    username: str
    layout_version: str
    key_secret: str
    slug_max_len: int


def load_git_publish_config() -> GitPublishConfig:
    enabled = os.getenv("GIT_PUBLISH_ENABLED", "true").strip().lower() in {"1", "true", "yes", "on"}
    return GitPublishConfig(
        enabled=enabled,
        repo_url=os.getenv("GIT_PUBLISH_REPO_URL", "").strip(),
        branch=os.getenv("GIT_PUBLISH_BRANCH", "main").strip(),
        base_path=os.getenv("GIT_PUBLISH_BASE_PATH", "artifacts").strip().strip("/"),
        author_name=os.getenv("GIT_PUBLISH_AUTHOR_NAME", "MissionControl Bot").strip(),
        author_email=os.getenv("GIT_PUBLISH_AUTHOR_EMAIL", "missioncontrol@local").strip(),
        token=os.getenv("GIT_PUBLISH_TOKEN"),
        username=os.getenv("GIT_PUBLISH_USERNAME", "x-access-token").strip(),
        layout_version=os.getenv("GIT_PUBLISH_LAYOUT_VERSION", "v2").strip().lower(),
        key_secret=os.getenv("GIT_PUBLISH_KEY_SECRET", "").strip(),
        slug_max_len=int(os.getenv("GIT_PUBLISH_SLUG_MAX_LEN", "48").strip() or "48"),
    )


def publish_artifact_record(
    *,
    artifact_id: int,
    mission_id: Optional[str],
    mission_name: Optional[str],
    kluster_id: str,
    artifact_name: str,
    artifact_uri: str,
    artifact_type: str,
    status: str,
    updated_at: datetime,
    config: Optional[GitPublishConfig] = None,
) -> Optional[dict]:
    cfg = config or load_git_publish_config()
    if not cfg.enabled:
        return None
    if not cfg.repo_url:
        raise GitPublishError("GIT_PUBLISH_REPO_URL is required when GIT_PUBLISH_ENABLED=true")

    with tempfile.TemporaryDirectory(prefix="missioncontrol-publish-") as tmpdir:
        repo_dir = Path(tmpdir) / "repo"
        _git_clone(repo_dir, cfg)
        _git_config_user(repo_dir, cfg)

        mission_segment = _mission_key_segment(mission_id=mission_id, mission_name=mission_name, secret=cfg.key_secret)
        rel_file, artifact_key = _artifact_rel_path(
            cfg=cfg,
            mission_segment=mission_segment,
            mission_id=mission_id,
            kluster_id=kluster_id,
            artifact_id=artifact_id,
            artifact_name=artifact_name,
        )
        target = repo_dir / rel_file
        target.parent.mkdir(parents=True, exist_ok=True)

        record = {
            "artifact_id": artifact_id,
            "mission_id": mission_id,
            "kluster_id": kluster_id,
            "name": artifact_name,
            "uri": artifact_uri,
            "artifact_type": artifact_type,
            "status": status,
            "updated_at": updated_at.isoformat(),
            "published_at": datetime.utcnow().isoformat(),
            "layout_version": cfg.layout_version,
            "artifact_key": artifact_key,
        }
        target.write_text(json.dumps(record, indent=2, sort_keys=True) + "\n", encoding="utf-8")

        _git(repo_dir, "add", str(rel_file))
        _git(
            repo_dir,
            "commit",
            "-m",
            f"publish artifact {artifact_id} (mission={mission_segment}, kluster={kluster_id}, layout={cfg.layout_version})",
            allow_empty=True,
        )
        _git(repo_dir, "push", "origin", cfg.branch)
        commit_sha = _git(repo_dir, "rev-parse", "HEAD").strip()

        return {
            "commit_sha": commit_sha,
            "path": str(rel_file),
            "branch": cfg.branch,
            "repo_url": _redact_repo_url(cfg.repo_url),
            "artifact_key": artifact_key,
            "layout_version": cfg.layout_version,
        }


def _git_clone(repo_dir: Path, cfg: GitPublishConfig) -> None:
    repo_url = _with_token(cfg.repo_url, cfg.username, cfg.token) if cfg.token else cfg.repo_url
    cmd = [
        "git",
        "clone",
        "--depth",
        "1",
        "--single-branch",
        "--branch",
        cfg.branch,
        repo_url,
        str(repo_dir),
    ]
    try:
        _run(cmd)
    except GitPublishError:
        # Allow first-time branch creation.
        _run(["git", "clone", "--depth", "1", repo_url, str(repo_dir)])
        _git(repo_dir, "checkout", "-B", cfg.branch)


def _git_config_user(repo_dir: Path, cfg: GitPublishConfig) -> None:
    _git(repo_dir, "config", "user.name", cfg.author_name)
    _git(repo_dir, "config", "user.email", cfg.author_email)


def _git(repo_dir: Path, *args: str, allow_empty: bool = False) -> str:
    cmd = ["git", *args]
    if allow_empty and args and args[0] == "commit":
        cmd.append("--allow-empty")
    return _run(cmd, cwd=repo_dir)


def _run(cmd: list[str], cwd: Optional[Path] = None) -> str:
    proc = subprocess.run(
        cmd,
        cwd=str(cwd) if cwd else None,
        check=False,
        capture_output=True,
        text=True,
        timeout=60,
    )
    if proc.returncode != 0:
        stderr = (proc.stderr or "").strip()
        stdout = (proc.stdout or "").strip()
        msg = stderr or stdout or f"command failed with exit code {proc.returncode}"
        raise GitPublishError(msg)
    return proc.stdout or ""


def _with_token(repo_url: str, username: str, token: str) -> str:
    if repo_url.startswith("https://"):
        host_and_path = repo_url[len("https://") :]
        return f"https://{quote(username, safe='')}:{quote(token, safe='')}@{host_and_path}"
    return repo_url


def _redact_repo_url(url: str) -> str:
    if "@" in url and "://" in url:
        scheme, rest = url.split("://", 1)
        if "@" in rest:
            rest = rest.split("@", 1)[1]
            return f"{scheme}://{rest}"
    return url


def _artifact_rel_path(
    *,
    cfg: GitPublishConfig,
    mission_segment: str,
    mission_id: Optional[str],
    kluster_id: str,
    artifact_id: int,
    artifact_name: str,
) -> tuple[Path, str]:
    if cfg.layout_version != "v2":
        return (
            Path(cfg.base_path) / mission_segment / "k" / kluster_id / "artifacts" / f"{artifact_id}.json",
            str(artifact_id),
        )

    artifact_key = _artifact_key(
        artifact_id=artifact_id,
        kluster_id=kluster_id,
        mission_id=mission_id,
        secret=cfg.key_secret,
    )
    slug = _slugify_name(artifact_name, max_len=cfg.slug_max_len)
    b1, b2 = artifact_key[:2], artifact_key[2:4]
    rel = (
        Path(cfg.base_path)
        / mission_segment
        / "k" / kluster_id
        / "artifacts"
        / b1
        / b2
        / f"{artifact_key}-{slug}.json"
    )
    return rel, artifact_key


def _artifact_key(*, artifact_id: int, kluster_id: str, mission_id: Optional[str], secret: str) -> str:
    seed = f"{artifact_id}:{kluster_id}:{mission_id if mission_id is not None else 'unassigned'}"
    if secret:
        seed = f"{secret}:{seed}"
    return hashlib.sha256(seed.encode("utf-8")).hexdigest()[:8]


def _slugify_name(name: str, *, max_len: int) -> str:
    slug = name.strip().lower()
    slug = re.sub(r"[^a-z0-9]+", "-", slug)
    slug = re.sub(r"-{2,}", "-", slug).strip("-")
    if not slug:
        slug = "artifact"
    return slug[: max(8, max_len)]


def _mission_key_segment(*, mission_id: Optional[str], mission_name: Optional[str], secret: str) -> str:
    if mission_id is None:
        return "unassigned-000000"
    slug = _slugify_name(mission_name or "mission", max_len=48)
    return f"{slug}-{mission_id}"


def _short_hash(entity_type: str, entity_id: int, secret: str) -> str:
    seed = f"{entity_type}:{entity_id}"
    if secret:
        seed = f"{secret}:{seed}"
    return hashlib.sha256(seed.encode("utf-8")).hexdigest()[:6]
