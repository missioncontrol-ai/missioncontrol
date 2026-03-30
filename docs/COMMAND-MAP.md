# Command Map

This is the authoritative `mc` CLI command hierarchy.

## Top Level

- `mc status`
- `mc doctor`
- `mc health`
- `mc version`
- `mc config`
- `mc use`
- `mc release`
- `mc logs`
- `mc completion`
- `mc auth`
- `mc admin`
- `mc data`
- `mc system`
- `mc agent`
- `mc approvals`
- `mc workspace`
- `mc ops`
- `mc daemon`
- `mc launch`
- `mc claude`
- `mc init`
- `mc serve`
- `mc profile`

## quick verbs

- `mc status [--verify-lease]` — combined auth/runtime/attached-workspace status; optional lease validation heartbeat.
- `mc doctor` — shortcut to `mc system doctor`.
- `mc health` — backend MCP health probe.
- `mc version` — local CLI version + backend reachability.
- `mc config` — effective local runtime config (redacted).
- `mc use --profile <name>` — activate/apply profile (API-backed profile flow).
- `mc use --kluster-id <id> [--lease-seconds N] [--workspace-label <label>]` — acquire workspace lease lock (API-backed).
- `mc use --release` — release current active lease.
- `mc release [--reason <text>] [--ignore-missing]` — top-level lease release shortcut.
- `mc logs` — local log tail helper (local-only utility).
- `mc completion <shell>` — shell completion generator (local-only utility).
- `mc claude run <profile>` — primary Claude launch path (profile runtime + safe auto-repair).
- `mc claude doctor <profile> [--fix]` — inspect/repair Claude runtime readiness.
- `mc claude exec <profile> -- [args]` — raw Claude passthrough in prepared runtime.

## auth

- `mc auth login`
- `mc auth whoami`
- `mc auth logout`

## admin

- `mc admin policy active`
- `mc admin policy versions`
- `mc admin policy events`
- `mc admin governance ...`

## data

- `mc data tools list`
- `mc data tools call --tool <name> --payload '<json>'`
- `mc data sync status ...`
- `mc data sync promote ...`
- `mc data explorer tree`
- `mc data explorer node ...`

## system

- `mc system doctor --fix`
- `mc system backup --target postgres|rustfs|all`
- `mc system profile-gc ...`
- `mc system update ...`
- `mc system compat ...`
- `mc system drift ...`

## agent

- `mc agent remote ...`
- `mc agent evolve ...`

## unchanged top-level domains

- `mc approvals ...`
- `mc workspace ...`
- `mc ops ...`
- `mc daemon ...`
- `mc launch ...`
- `mc claude ...`
- `mc init ...`
- `mc serve ...`
- `mc profile ...`

## Output Modes

- `--output human|json|jsonl`
- `--json` (alias for `--output json`)
- `MC_OUTPUT=human|json|jsonl`
