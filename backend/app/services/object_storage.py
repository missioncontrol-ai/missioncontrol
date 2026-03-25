import os
from dataclasses import dataclass
from typing import Any, Optional
from app.services.secrets import get_secret

try:
    import boto3
except Exception:  # pragma: no cover - allows non-S3 local test runs without boto3
    boto3 = None

try:
    from botocore.client import BaseClient
except Exception:  # pragma: no cover - allows non-S3 local test runs without botocore
    BaseClient = Any


@dataclass
class ObjectStorageConfig:
    endpoint: str
    region: str
    bucket: str
    access_key: str
    secret_key: str
    secure: bool


def _bool_env(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _env_first(*names: str, default: str = "") -> str:
    for name in names:
        value = get_secret(name)
        if value is not None and str(value).strip():
            return str(value).strip()
    return default


def _endpoint_with_scheme(endpoint: str, secure: bool) -> str:
    value = (endpoint or "").strip()
    if not value:
        return value
    if value.startswith("http://") or value.startswith("https://"):
        return value
    return f"{'https' if secure else 'http'}://{value}"


def load_object_storage_config() -> Optional[ObjectStorageConfig]:
    secure = _bool_env("MC_OBJECT_STORAGE_SECURE", True)
    endpoint = _endpoint_with_scheme(_env_first("MC_OBJECT_STORAGE_ENDPOINT"), secure)
    region = _env_first("MC_OBJECT_STORAGE_REGION", default="us-east-1")
    bucket = _env_first("MC_OBJECT_STORAGE_BUCKET")
    access_key = _env_first("MC_OBJECT_STORAGE_ACCESS_KEY", "MC_OBJECT_STORAGE_KEY")
    secret_key = _env_first("MC_OBJECT_STORAGE_ACCESS_SECRET", "MC_OBJECT_STORAGE_SECRET")
    if not endpoint or not bucket or not access_key or not secret_key:
        return None
    return ObjectStorageConfig(
        endpoint=endpoint,
        region=region,
        bucket=bucket,
        access_key=access_key,
        secret_key=secret_key,
        secure=secure,
    )


def object_storage_enabled() -> bool:
    return load_object_storage_config() is not None


def _client(cfg: ObjectStorageConfig) -> BaseClient:
    if boto3 is None:
        raise RuntimeError("boto3 is not installed")
    return boto3.client(
        "s3",
        endpoint_url=cfg.endpoint,
        region_name=cfg.region,
        aws_access_key_id=cfg.access_key,
        aws_secret_access_key=cfg.secret_key,
        use_ssl=cfg.secure,
    )


def _slug(value: str, fallback: str) -> str:
    cleaned = "".join(ch if ch.isalnum() else "-" for ch in (value or "").strip().lower())
    while "--" in cleaned:
        cleaned = cleaned.replace("--", "-")
    cleaned = cleaned.strip("-")
    return cleaned or fallback


def scoped_prefix(*, mission_id: str, kluster_id: str) -> str:
    mission = _slug(mission_id, "mission")
    kluster = _slug(kluster_id, "kluster")
    return f"missions/{mission}/klusters/{kluster}/"


def build_scoped_key(*, mission_id: str, kluster_id: str, entity: str, filename: str) -> str:
    entity_slug = _slug(entity, "objects")
    filename_slug = _slug(filename, "blob")
    return f"{scoped_prefix(mission_id=mission_id, kluster_id=kluster_id)}{entity_slug}/{filename_slug}"


def put_bytes(*, key: str, body: bytes, content_type: str) -> tuple[str, int]:
    cfg = load_object_storage_config()
    if cfg is None:
        raise RuntimeError("Object storage is not configured")
    client = _client(cfg)
    client.put_object(Bucket=cfg.bucket, Key=key, Body=body, ContentType=content_type)
    return f"s3://{cfg.bucket}/{key}", len(body)


def head_bucket() -> None:
    cfg = load_object_storage_config()
    if cfg is None:
        return
    client = _client(cfg)
    client.head_bucket(Bucket=cfg.bucket)


def get_bytes_from_uri(uri: str, *, expected_prefix: str | None = None) -> tuple[bytes, str]:
    cfg = load_object_storage_config()
    if cfg is None:
        raise RuntimeError("Object storage is not configured")
    bucket, key = parse_s3_uri(uri)
    if bucket != cfg.bucket:
        raise PermissionError("Artifact bucket is outside configured missioncontrol bucket")
    if expected_prefix and not key.startswith(expected_prefix):
        raise PermissionError("Artifact key is outside authorized mission/kluster scope")
    client = _client(cfg)
    obj = client.get_object(Bucket=bucket, Key=key)
    body = obj["Body"].read()
    content_type = str(obj.get("ContentType") or "application/octet-stream")
    return body, content_type


def presign_get_uri(uri: str, *, expires_seconds: int = 60, expected_prefix: str | None = None) -> str:
    cfg = load_object_storage_config()
    if cfg is None:
        raise RuntimeError("Object storage is not configured")
    bucket, key = parse_s3_uri(uri)
    if bucket != cfg.bucket:
        raise PermissionError("Artifact bucket is outside configured missioncontrol bucket")
    if expected_prefix and not key.startswith(expected_prefix):
        raise PermissionError("Artifact key is outside authorized mission/kluster scope")
    ttl = max(1, min(int(expires_seconds), 3600))
    client = _client(cfg)
    return str(
        client.generate_presigned_url(
            "get_object",
            Params={"Bucket": bucket, "Key": key},
            ExpiresIn=ttl,
        )
    )


def parse_s3_uri(uri: str) -> tuple[str, str]:
    value = (uri or "").strip()
    prefix = "s3://"
    if not value.startswith(prefix):
        raise ValueError("Not an s3 URI")
    rest = value[len(prefix) :]
    if "/" not in rest:
        raise ValueError("Invalid s3 URI")
    bucket, key = rest.split("/", 1)
    if not bucket or not key:
        raise ValueError("Invalid s3 URI")
    return bucket, key
