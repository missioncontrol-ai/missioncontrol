output "cluster_name" {
  value = google_container_cluster.missioncontrol.name
}

output "node_pool_id" {
  value = google_container_node_pool.missioncontrol.id
}

output "endpoint" {
  value = google_container_cluster.missioncontrol.endpoint
}

output "ca_certificate" {
  value = google_container_cluster.missioncontrol.master_auth[0].cluster_ca_certificate
}
