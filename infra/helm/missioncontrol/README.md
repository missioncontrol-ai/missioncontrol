# MissionControl Helm deployment

This chart deploys the MissionControl API within the managed cluster created by the Terraform modules.

## Configuration

- `values.yaml` holds defaults for the image, replica count, service type, and environment configuration (`config` map). Update or override these entries when installing the chart.
- `config.<KEY>` entries become keys inside a Kubernetes `ConfigMap` (`missioncontrol-{namespace}-config`). Sensitive values may be stored in the optional `secrets` map, which renders as a Kubernetes `Secret`.

When running the chart after Terraform:

```bash
helm upgrade --install missioncontrol infra/helm/missioncontrol \
  --values infra/helm/missioncontrol/values.yaml \
  --set config.POSTGRES_HOST=$(terraform output -raw postgres_hostname) \
  --set-string config.POSTGRES_PASSWORD="${POSTGRES_PASSWORD}" \
  --set-string config.MC_TOKEN="${MC_TOKEN}"
```

Set `POSTGRES_PASSWORD` and `MC_TOKEN` from your secrets store (e.g., GitHub Actions secrets) before deploying. The GitHub workflow also uses those secrets when invoking the Helm upgrade. The chart relies on the Terraform outputs for the PostgreSQL host and any other runtime-specific endpoints.
