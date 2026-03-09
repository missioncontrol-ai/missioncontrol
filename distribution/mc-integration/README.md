# mc-integration

Public bootstrap repo for fast MissionControl MCP integration.

Goal: a brand-new user can run one command, see proof-of-life immediately, and then connect/authenticate when ready.

## Quickstart

### macOS / Linux

```bash
curl -fsSL https://raw.githubusercontent.com/RyanMerlin/mc-integration/main/install.sh | bash
```

With endpoint + token:

```bash
curl -fsSL https://raw.githubusercontent.com/RyanMerlin/mc-integration/main/install.sh | bash -s -- \
  --endpoint https://missioncontrol.example.com \
  --token YOUR_TOKEN \
  --agent both
```

### Windows (PowerShell)

```powershell
iwr -UseBasicParsing https://raw.githubusercontent.com/RyanMerlin/mc-integration/main/install.ps1 | iex
```

With endpoint + token:

```powershell
& ([scriptblock]::Create((iwr -UseBasicParsing https://raw.githubusercontent.com/RyanMerlin/mc-integration/main/install.ps1).Content)) `
  -Endpoint https://missioncontrol.example.com `
  -Token YOUR_TOKEN `
  -Agent both
```

## What It Installs

- `missioncontrol-mcp` using:
1. PyPI package (`missioncontrol-mcp`) first
2. GitHub HTTPS fallback (`mc-integration/missioncontrol-mcp` subdirectory) if PyPI install fails
- User env file: `~/.missioncontrol-agent.env`
- Agent config snippets:
  - Codex: `~/.missioncontrol/config/codex.mcp.toml`
  - Claude: `~/.missioncontrol/config/claude.mcp.json`
- Doctor checks to confirm local install and optional endpoint connectivity

## Installer Flags

- Bash: `--endpoint <url>` `--token <token>` `--agent codex|claude|both` `--install-dir <dir>`
- PowerShell: `-Endpoint <url>` `-Token <token>` `-Agent codex|claude|both` `-InstallDir <dir>`
- Default endpoint is `http://localhost:8008`; default install dir is `~/.missioncontrol`

## Authentication Handoff

Installer does not force auth. If no endpoint is supplied, installer defaults to localhost (`http://localhost:8008`) for immediate dry-run validation.

It prints:
- exact `source ~/.missioncontrol-agent.env` step
- exact `missioncontrol-mcp doctor` step
- guidance for adding token later and reconnecting

## Config API Contract

Generated configs use these env vars:
- `MC_BASE_URL`
- `MC_TOKEN`

So users can rotate endpoint/token without regenerating config.

## Version Matrix

| mc-integration | missioncontrol-mcp |
|---|---|
| `v0.1.x` | `>=0.1.0` |

## Local Development

```bash
bash install.sh --agent both
bash scripts/doctor.sh
```

`missioncontrol-mcp` package source in this repo lives under:

- `missioncontrol-mcp/`

## CI

- Shell smoke checks on Linux
- PowerShell parse + runtime smoke checks on Windows

## Release Quality Gate

Before publishing, run the release checklist:

- [docs/RELEASE-CHECKLIST.md](docs/RELEASE-CHECKLIST.md)

## Publishing `missioncontrol-mcp` to PyPI

See [docs/PYPI-PUBLISH.md](docs/PYPI-PUBLISH.md) for a practical release workflow.
