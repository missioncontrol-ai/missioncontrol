use axum::{extract::FromRequestParts, http::request::Parts};
use std::env;

/// Caller identity extracted from request headers.
pub struct Principal {
    pub subject: String,
    pub is_admin: bool,
}

impl<S> FromRequestParts<S> for Principal
where
    S: Send + Sync,
{
    type Rejection = std::convert::Infallible;

    async fn from_request_parts(parts: &mut Parts, _state: &S) -> Result<Self, Self::Rejection> {
        let admin_token = env::var("MC_TOKEN").ok();
        let bearer = parts
            .headers
            .get("authorization")
            .and_then(|v| v.to_str().ok())
            .and_then(|s| s.strip_prefix("Bearer "))
            .map(|s| s.trim().to_string());

        let is_admin = matches!((&admin_token, &bearer), (Some(t), Some(b)) if t == b);

        let subject = parts
            .headers
            .get("x-mc-agent-id")
            .and_then(|v| v.to_str().ok())
            .map(|s| s.trim().to_string())
            .or(bearer.clone())
            .unwrap_or_else(|| "anonymous".into());

        Ok(Principal { subject, is_admin })
    }
}
