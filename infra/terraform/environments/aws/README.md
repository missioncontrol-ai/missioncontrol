# AWS Environment

This directory reuses the shared Terraform module contracts, but with the `aws` provider so the multi-cloud footprint remains aligned.

Key elements already implemented here:

- Private VPC and subnet with tagging plus security group for Postgres.
- EKS cluster and node group module inputs bound to provided IAM role ARNs and subnet IDs.
- Amazon RDS PostgreSQL multi-AZ instance, security group, and Secrets Manager namespace.
- Terraform backend (S3 bucket + DynamoDB locking) so state sharing matches the pattern from Azure/GCP.
