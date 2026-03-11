output "network_id" {
  value = module.network.network_id
}

output "kubernetes_cluster" {
  value = module.kubernetes.cluster_name
}

output "postgres_instance" {
  value = module.postgres.instance_id
}

output "secret_name" {
  value = module.secrets.secret_name
}
