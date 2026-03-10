/// Lightweight carrier that keeps agent identifiers and approval hints together.
#[derive(Clone, Debug, Default)]
pub struct AgentContext {
    pub agent_id: Option<String>,
}

impl AgentContext {
    pub fn new(agent_id: Option<String>) -> Self {
        Self { agent_id }
    }
}
