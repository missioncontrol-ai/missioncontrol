# mc-integration Subtree Release

`missioncontrol` is the canonical source for public integration distribution assets under:

- `distribution/mc-integration`

Public publishing target:

- `https://github.com/RyanMerlin/mc-integration`

## One-time remote setup

```bash
git remote add mc-integration https://github.com/RyanMerlin/mc-integration.git
```

## Validate local export content

```bash
bash scripts/sync-mc-integration-mcp.sh
find distribution/mc-integration -maxdepth 3 -type f
```

## Dry run split

```bash
DRY_RUN=1 bash scripts/release-mc-integration-subtree.sh
```

The script requires tracked files under `distribution/mc-integration` (commit or stage them first).

## Publish subtree to public main

```bash
bash scripts/release-mc-integration-subtree.sh
```

Optional overrides:

- `MC_INTEGRATION_EXPORT_PREFIX` (default: `distribution/mc-integration`)
- `MC_INTEGRATION_REMOTE` (default: `mc-integration`)
- `MC_INTEGRATION_BRANCH` (default: `main`)
- `MC_INTEGRATION_SPLIT_BRANCH` (default: `tmp/mc-integration-subtree`)

## Operating rules

- Edit integration distribution content only in `missioncontrol/distribution/mc-integration`.
- Do not directly edit `mc-integration` public repo.
- Tag releases in the public repo after subtree push.
