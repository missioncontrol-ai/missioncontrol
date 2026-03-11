output "vpc_id" {
  value = aws_vpc.missioncontrol.id
}

output "subnet_id" {
  value = aws_subnet.missioncontrol.id
}
