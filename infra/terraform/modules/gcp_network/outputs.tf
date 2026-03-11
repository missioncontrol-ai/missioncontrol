output "network_id" {
  value = google_compute_network.missioncontrol.id
}

output "subnetwork_id" {
  value = google_compute_subnetwork.missioncontrol.id
}
