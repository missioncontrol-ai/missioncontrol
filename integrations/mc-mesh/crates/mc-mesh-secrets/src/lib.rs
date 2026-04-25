pub mod config;
pub mod error;
pub mod types;
pub mod client;
pub mod redact;
pub mod resolver;

#[cfg(target_os = "linux")]
pub mod keyring;

pub use config::InfisicalConfig;
pub use error::{SecretsError, Result};
pub use types::{CredentialSource, CredentialKind, ResolvedCredentials};
pub use redact::SecretRedactor;
pub use resolver::resolve_credentials;

#[cfg(target_os = "linux")]
pub use keyring::{store_service_token, load_service_token, delete_service_token, KeyringResult};
