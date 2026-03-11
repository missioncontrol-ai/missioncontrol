output "vpc_id" {
  value = module.network.vpc_id
}

output "eks_cluster" {
  value = module.kubernetes.cluster_name
}

output "db_endpoint" {
  value = module.postgres.endpoint
}

output "secret_arn" {
  value = module.secrets.secret_arn
}
