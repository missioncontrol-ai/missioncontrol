use mc_mesh_core::client::BackendClient;
use mc_mesh_core::types::{Capability, MeshTaskRecord};
use anyhow::Result;

/// Result of a successful claim attempt.
pub struct ClaimOutcome {
    pub task: MeshTaskRecord,
    pub claim_lease_id: Option<String>,
}

/// Filter a list of tasks to those matching the given capabilities.
pub fn filter_eligible<'a>(tasks: &'a [MeshTaskRecord], caps: &[Capability]) -> Vec<&'a MeshTaskRecord> {
    tasks
        .iter()
        .filter(|t| {
            t.required_capabilities
                .iter()
                .all(|req| caps.iter().any(|c| c.0 == *req))
        })
        .collect()
}

/// Find and claim the highest-priority eligible task. Returns the claimed record
/// together with the `claim_lease_id`, or `None` if nothing was claimable.
pub async fn try_claim_one(
    client: &BackendClient,
    kluster_id: &str,
    caps: &[Capability],
) -> Result<Option<ClaimOutcome>> {
    let tasks = crate::task::poll_ready_tasks(client, kluster_id, caps).await?;
    let eligible = filter_eligible(&tasks, caps);
    let Some(candidate) = eligible.first() else {
        return Ok(None);
    };

    // Best-effort claim — another agent may race us; that's fine.
    match crate::task::claim_task(client, &candidate.id).await {
        Ok(result) => {
            let mut task = result.task;
            task.status = "claimed".into();
            Ok(Some(ClaimOutcome { task, claim_lease_id: result.claim_lease_id }))
        }
        Err(e) => {
            tracing::debug!("claim race lost for task {}: {e}", candidate.id);
            Ok(None)
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn task(id: &str, required_caps: &[&str], claim_policy: &str) -> MeshTaskRecord {
        MeshTaskRecord {
            id: id.to_string(),
            kluster_id: "k1".to_string(),
            mission_id: "m1".to_string(),
            title: id.to_string(),
            description: String::new(),
            status: "ready".to_string(),
            claim_policy: claim_policy.to_string(),
            required_capabilities: required_caps.iter().map(|s| s.to_string()).collect(),
            lease_expires_at: None,
            claim_lease_id: None,
        }
    }

    fn caps(names: &[&str]) -> Vec<Capability> {
        names.iter().map(|s| Capability::new(*s)).collect()
    }

    #[test]
    fn no_caps_required_always_eligible() {
        let tasks = vec![task("t1", &[], "first_claim")];
        let agent_caps = caps(&["code.edit"]);
        let eligible = filter_eligible(&tasks, &agent_caps);
        assert_eq!(eligible.len(), 1);
    }

    #[test]
    fn matching_caps_eligible() {
        let tasks = vec![task("t1", &["code.edit", "test.run"], "first_claim")];
        let agent_caps = caps(&["code.edit", "test.run", "code.read"]);
        let eligible = filter_eligible(&tasks, &agent_caps);
        assert_eq!(eligible.len(), 1);
    }

    #[test]
    fn missing_one_cap_not_eligible() {
        let tasks = vec![task("t1", &["code.edit", "test.run"], "first_claim")];
        let agent_caps = caps(&["code.edit"]); // missing test.run
        let eligible = filter_eligible(&tasks, &agent_caps);
        assert!(eligible.is_empty());
    }

    #[test]
    fn partial_match_across_tasks() {
        let tasks = vec![
            task("t1", &["gemini"], "first_claim"),
            task("t2", &["code.edit"], "first_claim"),
            task("t3", &[], "first_claim"),
        ];
        let agent_caps = caps(&["code.edit"]);
        let eligible = filter_eligible(&tasks, &agent_caps);
        // t1 needs gemini (missing), t2 and t3 match
        assert_eq!(eligible.len(), 2);
        assert!(eligible.iter().any(|t| t.id == "t2"));
        assert!(eligible.iter().any(|t| t.id == "t3"));
    }

    #[test]
    fn empty_task_list_returns_empty() {
        let eligible = filter_eligible(&[], &caps(&["code.edit"]));
        assert!(eligible.is_empty());
    }

    #[test]
    fn empty_agent_caps_only_matches_no_requirement_tasks() {
        let tasks = vec![
            task("t1", &["code.edit"], "first_claim"),
            task("t2", &[], "first_claim"),
        ];
        let eligible = filter_eligible(&tasks, &[]);
        assert_eq!(eligible.len(), 1);
        assert_eq!(eligible[0].id, "t2");
    }
}
