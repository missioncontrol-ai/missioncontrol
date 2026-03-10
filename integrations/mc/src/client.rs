use crate::config::McConfig;
use anyhow::{Context, Result};
use reqwest::{Client, Method, RequestBuilder};
use serde_json::Value;
use url::Url;

/// HTTP client that keeps auth headers, retries, and streaming helpers in one place.
#[derive(Clone)]
pub struct MissionControlClient {
    http: Client,
    base_url: Url,
    token: Option<String>,
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
        })
    }

    fn apply_auth(&self, builder: RequestBuilder) -> RequestBuilder {
        if let Some(token) = &self.token {
            builder.bearer_auth(token)
        } else {
            builder
        }
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
}
