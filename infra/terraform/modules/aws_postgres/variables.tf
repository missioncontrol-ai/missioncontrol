variable "name_prefix" {
  type = string
}

variable "instance_identifier" {
  type = string
}

variable "instance_class" {
  type    = string
  default = "db.t3.medium"
}

variable "storage_gb" {
  type    = number
  default = 20
}

variable "database_name" {
  type = string
}

variable "username" {
  type = string
}

variable "password" {
  type      = string
  sensitive = true
}

variable "subnet_ids" {
  type = list(string)
}

variable "security_group_ids" {
  type = list(string)
}
