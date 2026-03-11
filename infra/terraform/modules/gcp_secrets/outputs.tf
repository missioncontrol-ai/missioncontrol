output "secret_name" {
  value = google_secret_manager_secret.missioncontrol.name
}

output "secret_id" {
  value = google_secret_manager_secret.missioncontrol.secret_id
}
