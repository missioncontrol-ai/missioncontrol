use crate::{booster::AgentBooster, client::MissionControlClient, schema_pack::SchemaPack};
use anyhow::{Context, Result};
use serde_json::{json, Value};

/// Fetch the raw tools list from the backend's /mcp/tools endpoint.
/// Shared by the MCP server cache and background warm-up.
pub async fn fetch_tools_from_backend(client: &MissionControlClient) -> Result<Vec<Value>> {
    let response = client
        .get_json("/mcp/tools")
        .await
        .context("failed to fetch tools from backend")?;

    let tools: Vec<Value> = match response {
        Value::Array(arr) => arr,
        Value::Object(ref obj) => obj
            .get("tools")
            .and_then(|t| t.as_array())
            .cloned()
            .unwrap_or_default(),
        _ => Vec::new(),
    };
    Ok(tools)
}

pub async fn call_tool(
    client: &MissionControlClient,
    booster: Option<&AgentBooster>,
    schema_pack: Option<&SchemaPack>,
    tool: &str,
    args: Value,
) -> Result<Value> {
    if let Some(schema_pack) = schema_pack {
        schema_pack
            .validate_payload(&args)
            .context("schema pack validation failed")?;
    }

    if let Some(booster) = booster {
        if booster.is_enabled() {
            let short_circuit = booster.run(&args).context("booster validation failed")?;
            if short_circuit {
                if booster.allow_short_circuit() {
                    println!("[booster] short-circuited {tool}");
                    return Ok(json!({ "booster_short_circuit": true, "tool": tool }));
                }
                println!(
                    "[booster] short-circuit requested for {tool} but disabled; forwarding to Mission Control"
                );
            }
        }
    }

    let request = json!({
        "tool": tool,
        "args": args,
    });
    client.post_json("/mcp/call", &request).await
}
