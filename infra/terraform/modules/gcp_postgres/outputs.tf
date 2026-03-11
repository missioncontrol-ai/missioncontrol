output "connection_name" {
  value = google_sql_database_instance.missioncontrol.connection_name
}

output "instance_id" {
  value = google_sql_database_instance.missioncontrol.id
}

output "ip_address" {
  value = google_sql_database_instance.missioncontrol.ip_address[0].ip_address
}
