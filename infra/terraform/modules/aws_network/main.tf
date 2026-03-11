resource "aws_vpc" "missioncontrol" {
  cidr_block           = var.cidr_block
  enable_dns_support   = true
  enable_dns_hostnames = true
  tags                 = var.tags
}

resource "aws_subnet" "missioncontrol" {
  vpc_id     = aws_vpc.missioncontrol.id
  cidr_block = var.subnet_cidr
  availability_zone = var.availability_zone
  tags       = var.tags
}
