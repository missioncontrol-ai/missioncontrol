use chrono::{DateTime, Utc};
use serde::{Deserialize, Serialize};

#[derive(Debug, Clone, Serialize, Deserialize, sqlx::FromRow)]
pub struct Task {
    pub id: i64,
    pub public_id: String,
    pub kluster_id: String,
    pub epic_id: Option<i64>,
    pub title: String,
    pub description: String,
    pub status: String,
    pub owner: String,
    pub contributors: String,
    pub dependencies: String,
    pub definition_of_done: String,
    pub related_artifacts: String,
    pub created_at: DateTime<Utc>,
    pub updated_at: DateTime<Utc>,
}

#[derive(Debug, Deserialize)]
pub struct TaskCreate {
    pub title: String,
    #[serde(default)]
    pub description: String,
    #[serde(default = "default_proposed")]
    pub status: String,
    #[serde(default)]
    pub owner: String,
    #[serde(default)]
    pub contributors: String,
    #[serde(default)]
    pub dependencies: String,
    #[serde(default)]
    pub definition_of_done: String,
    #[serde(default)]
    pub related_artifacts: String,
    pub kluster_id: Option<String>,
    pub epic_id: Option<i64>,
}

#[derive(Debug, Deserialize)]
pub struct TaskUpdate {
    pub title: Option<String>,
    pub description: Option<String>,
    pub status: Option<String>,
    pub owner: Option<String>,
    pub contributors: Option<String>,
    pub dependencies: Option<String>,
    pub definition_of_done: Option<String>,
    pub related_artifacts: Option<String>,
    pub epic_id: Option<i64>,
}

fn default_proposed() -> String { "proposed".into() }
