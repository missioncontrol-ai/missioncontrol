use crate::config::McConfig;
use anyhow::{Context, Result};
use reqwest::{Client, Method, RequestBuilder};
use serde_json::Value;
use std::time::Duration;
use url::Url;

/// HTTP client that keeps auth headers, retries, and streaming helpers in one place.
#[derive(Clone)]
pub struct MissionControlClient {
    http: Client,
    base_url: Url,
    token: Option<String>,
    agent_id: Option<String>,
    runtime_session_id: Option<String>,
    profile_name: Option<String>,
}

impl MissionControlClient {
    pub fn new(config: &McConfig) -> Result<Self> {
        let mut builder = Client::builder().timeout(config.timeout);
        if config.allow_insecure {
            builder = builder.danger_accept_invalid_certs(true);
        }

        Ok(Self {
            http: builder.build()?,
            base_url: config.base_url.clone(),
            token: config.token.clone(),
            agent_id: config.agent_context.agent_id.clone(),
            runtime_session_id: config.agent_context.runtime_session_id.clone(),
            profile_name: config.agent_context.profile_name.clone(),
        })
    }

    /// Build a minimal client from a raw base URL + token, used during `mc auth login`
    /// before a full McConfig is available.
    pub fn new_with_token(base_url: &str, token: &str) -> Result<Self> {
        let http = Client::builder().timeout(Duration::from_secs(15)).build()?;
        Ok(Self {
            http,
            base_url: Url::parse(base_url).context("invalid base URL")?,
            token: if token.is_empty() {
                None
            } else {
                Some(token.to_string())
            },
            agent_id: None,
            runtime_session_id: None,
            profile_name: None,
        })
    }

    fn apply_auth(&self, builder: RequestBuilder) -> RequestBuilder {
        let mut request = if let Some(token) = &self.token {
            builder.bearer_auth(token)
        } else {
            builder
        };
        if let Some(agent_id) = &self.agent_id {
            if !agent_id.trim().is_empty() {
                request = request.header("x-mc-agent-id", agent_id);
            }
        }
        if let Some(runtime_session_id) = &self.runtime_session_id {
            if !runtime_session_id.trim().is_empty() {
                request = request.header("x-mc-runtime-session-id", runtime_session_id);
                request = request.header("x-mc-instance-id", runtime_session_id);
            }
        }
        if let Some(profile_name) = &self.profile_name {
            if !profile_name.trim().is_empty() {
                request = request.header("x-mc-agent-profile", profile_name);
            }
        }
        request
    }

    pub fn request_builder(&self, method: Method, path: &str) -> Result<RequestBuilder> {
        let url = self
            .base_url
            .join(path)
            .with_context(|| format!("invalid endpoint path: {path}"))?;
        Ok(self.apply_auth(self.http.request(method, url)))
    }

    pub async fn get_json(&self, path: &str) -> Result<Value> {
        let resp = self.request_builder(Method::GET, path)?;
        let resp = resp
            .send()
            .await
            .context("request failed")?
            .error_for_status()
            .context("unexpected status code")?;
        resp.json::<Value>()
            .await
            .context("unable to parse json response")
    }

    pub async fn post_json(&self, path: &str, body: &Value) -> Result<Value> {
        let resp = self.request_builder(Method::POST, path)?;
        let resp = resp
            .json(body)
            .send()
            .await
            .context("request failed")?
            .error_for_status()
            .context("unexpected status code")?;
        resp.json::<Value>()
            .await
            .context("unable to parse json response")
    }

    pub async fn put_json(&self, path: &str, body: &Value) -> Result<Value> {
        let resp = self.request_builder(Method::PUT, path)?;
        let resp = resp
            .json(body)
            .send()
            .await
            .context("request failed")?
            .error_for_status()
            .context("unexpected status code")?;
        resp.json::<Value>()
            .await
            .context("unable to parse json response")
    }

    pub async fn delete(&self, path: &str) -> Result<()> {
        self.request_builder(Method::DELETE, path)?
            .send()
            .await
            .context("request failed")?
            .error_for_status()
            .context("unexpected status code")?;
        Ok(())
    }

    pub fn ws_url(&self, path: &str) -> Result<url::Url> {
        let mut url = self.base_url.clone();
        match url.scheme() {
            "http" => {
                url.set_scheme("ws").ok();
            }
            "https" => {
                url.set_scheme("wss").ok();
            }
            _ => {}
        }
        url = url
            .join(path)
            .with_context(|| format!("invalid websocket path: {path}"))?;
        Ok(url)
    }
}
