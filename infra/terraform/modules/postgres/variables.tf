variable "location" {
  type = string
}

variable "resource_group_name" {
  type = string
}

variable "server_name" {
  type = string
}

variable "admin_login" {
  type = string
}

variable "admin_password" {
  type      = string
  sensitive = true
}

variable "sku_name" {
  type    = string
  default = "Standard_D2s_v3"
}

variable "storage_mb" {
  type    = number
  default = 51200
}

variable "subnet_id" {
  type = string
}
