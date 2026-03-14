import os
from dataclasses import dataclass
from typing import Literal

AuthMode = Literal["token", "oidc", "dual"]


def _as_bool(value: str | None, default: bool = False) -> bool:
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _env_first(*names: str) -> str | None:
    for name in names:
        value = os.getenv(name)
        if value is not None and str(value).strip():
            return str(value).strip()
    return None


@dataclass(frozen=True)
class AuthSettings:
    mode: AuthMode
    missioncontrol_token: str | None
    oidc_issuer_url: str | None
    oidc_audience: str | None
    oidc_jwks_url: str | None
    oidc_client_id: str | None
    oidc_client_secret: str | None
    oidc_redirect_uri: str | None
    oidc_scopes: str
    oidc_required: bool

    def token_enabled(self) -> bool:
        return self.mode in {"token", "dual"} and bool(self.missioncontrol_token)

    def oidc_enabled(self) -> bool:
        return bool(self.oidc_issuer_url and self.oidc_audience)

    def validate_startup(self) -> None:
        if self.mode == "token" and not self.missioncontrol_token:
            raise RuntimeError("MC_TOKEN is required when AUTH_MODE=token")

        if self.mode == "oidc":
            if not self.oidc_issuer_url:
                raise RuntimeError("OIDC_ISSUER_URL is required when AUTH_MODE=oidc")
            if not self.oidc_audience:
                raise RuntimeError("OIDC_AUDIENCE is required when AUTH_MODE=oidc")

        if self.oidc_required and not self.oidc_enabled():
            raise RuntimeError("OIDC_REQUIRED=true requires both OIDC_ISSUER_URL and OIDC_AUDIENCE")


def load_auth_settings() -> AuthSettings:
    mode_env = os.getenv("AUTH_MODE")
    if mode_env is not None:
        mode = mode_env.strip().lower()
    else:
        has_oidc = bool(os.getenv("OIDC_ISSUER_URL") and os.getenv("OIDC_AUDIENCE"))
        has_token = bool(_env_first("MC_TOKEN"))
        if has_oidc:
            mode = "oidc"
        elif has_token:
            mode = "token"
        else:
            mode = "oidc"
    if mode not in {"token", "oidc", "dual"}:
        raise RuntimeError("AUTH_MODE must be one of: token, oidc, dual")

    settings = AuthSettings(
        mode=mode,
        missioncontrol_token=_env_first("MC_TOKEN"),
        oidc_issuer_url=os.getenv("OIDC_ISSUER_URL"),
        oidc_audience=os.getenv("OIDC_AUDIENCE"),
        oidc_jwks_url=os.getenv("OIDC_JWKS_URL"),
        oidc_client_id=_env_first("OIDC_CLIENT_ID", "OIDC_AUDIENCE"),
        oidc_client_secret=_env_first("OIDC_CLIENT_SECRET"),
        oidc_redirect_uri=_env_first("OIDC_REDIRECT_URI"),
        oidc_scopes=_env_first("OIDC_SCOPES") or "openid profile email",
        oidc_required=_as_bool(os.getenv("OIDC_REQUIRED"), default=False),
    )
    settings.validate_startup()
    return settings
