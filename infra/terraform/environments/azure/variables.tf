variable "subscription_id" {
  type = string
}

variable "tenant_id" {
  type = string
}

variable "resource_group_name" {
  type = string
}

variable "location" {
  type = string
}

variable "tags" {
  type    = map(string)
  default = {}
}

variable "subnet_prefix" {
  type    = string
  default = "10.0.1.0/24"
}

variable "kubernetes_node_count" {
  type    = number
  default = 2
}

variable "kubernetes_node_size" {
  type    = string
  default = "Standard_D2s_v3"
}

variable "postgres_admin_login" {
  type = string
}

variable "postgres_admin_password" {
  type      = string
  sensitive = true
}

variable "creator_object_id" {
  type = string
}

variable "backend_storage_account" {
  type = string
}

variable "backend_container" {
  type = string
}
