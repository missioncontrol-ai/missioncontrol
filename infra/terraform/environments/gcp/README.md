# GCP Environment

This directory consumes the shared Terraform modules with the `google` provider using the same input/output contracts as Azure so the control plane remains consistent.

Key additions planned:

- Private GKE cluster with `master_authorized_networks` and Cloud NAT.
- Cloud SQL PostgreSQL instance with Private Service Connect and backups.
- Secret Manager + External Secrets Operator integration.
- Terraform state backend (Google Cloud Storage bucket + locking).

The directory already defines provider wiring, module invocation, variables, backend configuration, and outputs, making it ready once real values are supplied.
