variable "key_vault_name" {
  type = string
}

variable "location" {
  type = string
}

variable "resource_group_name" {
  type = string
}

variable "tenant_id" {
  type = string
}

variable "creator_object_id" {
  type = string
}

variable "purge_protection" {
  type    = bool
  default = true
}
