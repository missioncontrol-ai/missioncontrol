use crate::client::MissionControlClient;
use anyhow::Result;
use clap::{Args, Subcommand};
use serde_json::{Value, json};

#[derive(Subcommand, Debug)]
pub enum RemoteCommand {
    /// Send messages between agents.
    Message(RemoteMessageArgs),
    /// Manage agent sessions.
    #[command(subcommand)]
    Sessions(RemoteSessionsCommand),
}

#[derive(Args, Debug)]
pub struct RemoteMessageArgs {
    #[arg(long)]
    pub agent_id: i32,
    #[arg(long)]
    pub to_agent_id: i32,
    #[arg(long)]
    pub content: String,
    #[arg(long, default_value = "command")]
    pub message_type: String,
    #[arg(long)]
    pub task_id: Option<i32>,
}

#[derive(Subcommand, Debug)]
pub enum RemoteSessionsCommand {
    /// List recent sessions for an agent.
    List(RemoteSessionsListArgs),
    /// Start a new session for an agent.
    Start(RemoteSessionStartArgs),
    /// End a session.
    End(RemoteSessionEndArgs),
}

#[derive(Args, Debug)]
pub struct RemoteSessionsListArgs {
    #[arg(long)]
    pub agent_id: i32,
    #[arg(long, default_value_t = 20)]
    pub limit: u32,
}

#[derive(Args, Debug)]
pub struct RemoteSessionStartArgs {
    #[arg(long)]
    pub agent_id: i32,
    #[arg(long)]
    pub context: String,
}

#[derive(Args, Debug)]
pub struct RemoteSessionEndArgs {
    #[arg(long)]
    pub agent_id: i32,
    #[arg(long)]
    pub session_id: i32,
}

pub async fn run(command: RemoteCommand, client: &MissionControlClient) -> Result<()> {
    match command {
        RemoteCommand::Message(args) => run_message(args, client).await,
        RemoteCommand::Sessions(subcommand) => run_sessions(subcommand, client).await,
    }
}

async fn run_message(args: RemoteMessageArgs, client: &MissionControlClient) -> Result<()> {
    let payload = json!({
        "to_agent_id": args.to_agent_id,
        "content": args.content,
        "message_type": args.message_type,
        "task_id": args.task_id,
    });
    let path = format!("/agents/{}/message", args.agent_id);
    let response = client.post_json(&path, &payload).await?;
    print_json(&response);
    Ok(())
}

async fn run_sessions(command: RemoteSessionsCommand, client: &MissionControlClient) -> Result<()> {
    match command {
        RemoteSessionsCommand::List(args) => {
            let path = format!("/agents/{}/sessions?limit={}", args.agent_id, args.limit);
            let response = client.get_json(&path).await?;
            print_json(&response);
        }
        RemoteSessionsCommand::Start(args) => {
            let payload = json!({ "context": args.context });
            let path = format!("/agents/{}/sessions", args.agent_id);
            let response = client.post_json(&path, &payload).await?;
            print_json(&response);
        }
        RemoteSessionsCommand::End(args) => {
            let path = format!("/agents/{}/sessions/{}/end", args.agent_id, args.session_id);
            let response = client.post_json(&path, &json!({})).await?;
            print_json(&response);
        }
    }
    Ok(())
}

fn print_json(value: &Value) {
    println!(
        "{}",
        serde_json::to_string_pretty(value).unwrap_or_else(|_| value.to_string())
    );
}
