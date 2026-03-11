resource "aws_secretsmanager_secret" "missioncontrol" {
  name = var.secret_name
}
