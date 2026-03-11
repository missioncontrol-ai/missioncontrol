output "db_instance_id" {
  value = aws_db_instance.missioncontrol.id
}

output "endpoint" {
  value = aws_db_instance.missioncontrol.endpoint
}
