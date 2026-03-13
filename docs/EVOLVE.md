# Evolve — MissionControl Self-Improvement Loop

`mc evolve` is the mechanism by which MissionControl uses its own agent infrastructure to improve itself. An evolve mission is a pre-configured mission where the codebase context is injected, a backlog of improvement tasks is seeded, and agents autonomously implement them.

## Quick Start

```bash
# 1. Seed a mission with a task backlog
mc evolve seed --spec docs/evolve-seed-spec.json
# → outputs mission_id: evolve-abc12345

# 2. Launch an agent against the mission
mc evolve run --mission evolve-abc12345 --agent claude

# 3. Check progress
mc evolve status --mission evolve-abc12345
```

## Spec File Format

The spec file is JSON:

```json
{
  "name": "MC self-improvement sprint 1",
  "description": "Improve pressure test latency and add evolve loop",
  "agent_system_prompt": "You are an expert Rust and Python engineer improving MissionControl. Work from the task backlog. Each task should result in a passing test. Commit your changes.",
  "scoring_criteria": {
    "tests_passing": true,
    "builds_clean": true,
    "diff_reviewable": true
  },
  "tasks": [
    {
      "id": "t001",
      "title": "Add latency percentiles to pressure test",
      "description": "Add P50/P95/P99 latency tracking to mc-pressure-test.sh",
      "acceptance": "summary.json contains latency_p95_ms field"
    },
    {
      "id": "t002",
      "title": "Add mc evolve CLI",
      "description": "Implement mc evolve seed/run/status subcommands",
      "acceptance": "mc evolve --help shows seed, run, status"
    }
  ]
}
```

## Subcommands

| Command | Description |
|---------|-------------|
| `mc evolve seed --spec <file>` | Seed an evolve mission from a JSON spec. Outputs `mission_id`. |
| `mc evolve run --mission <id> [--agent <name>]` | Launch an agent (default: claude) against the evolve mission. |
| `mc evolve status --mission <id>` | Show current status, run count, and task progress. |

## How It Works

1. **seed** — creates a mission record in the backend with your task backlog and agent system prompt injected as context.
2. **run** — calls `mc launch <agent>` (or the backend `/evolve/missions/<id>/run` endpoint) with the evolve mission's context pre-loaded. The agent picks tasks from the backlog, implements them, and reports back.
3. **status** — polls the backend for progress: which tasks are complete, which agent ran them, and any scores or artifacts produced.

## Agent Roles

| Agent | Best For |
|-------|----------|
| claude | Complex refactors, architecture changes, test authoring |
| codex | Fast iteration on well-defined code tasks |
| gemini | Documentation, multi-file review |
| openclaw | Parallel sub-task execution |

## Guardrails

- Evolve missions are scoped to `owner_subject` — agents cannot touch other users' data.
- The injected system prompt is append-only; it cannot override safety policies.
- All agent outputs are committed to mission artifacts for audit.
- A `scoring_criteria` block in the spec defines what constitutes a passing run (tests, build, diff size).

## Backend API

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/evolve/missions` | POST | Seed a new evolve mission |
| `/evolve/missions/{id}/run` | POST | Launch agent run |
| `/evolve/missions/{id}/status` | GET | Get mission progress |

## Roadmap

- [ ] Persist evolve missions to the database (currently in-memory)
- [ ] Score runs automatically (parse test results, build status from artifacts)
- [ ] `mc evolve watch` — stream agent output in real time
- [ ] Leaderboard: which agent completed the most tasks with the highest scores
