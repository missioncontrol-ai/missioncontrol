# Command Map

This is the authoritative `mc` CLI command hierarchy.

## Top Level

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
- `mc init`
- `mc serve`
- `mc profile`

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
- `mc init ...`
- `mc serve ...`
- `mc profile ...`

## Output Modes

- `--output human|json|jsonl`
- `--json` (alias for `--output json`)
- `MC_OUTPUT=human|json|jsonl`
