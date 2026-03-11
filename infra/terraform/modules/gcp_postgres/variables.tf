variable "project" {
  type = string
}

variable "region" {
  type = string
}

variable "instance_name" {
  type = string
}

variable "tier" {
  type    = string
  default = "db-custom-1-3840"
}

variable "disk_size" {
  type    = number
  default = 50
}

variable "network_self_link" {
  type = string
}
