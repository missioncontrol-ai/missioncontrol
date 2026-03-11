variable "project" {
  type = string
}

variable "region" {
  type = string
}

variable "zone" {
  type = string
}

variable "subnetwork_cidr" {
  type    = string
  default = "10.10.0.0/24"
}

variable "kubernetes_node_count" {
  type    = number
  default = 2
}

variable "kubernetes_node_type" {
  type    = string
  default = "e2-standard-2"
}

variable "postgres_tier" {
  type    = string
  default = "db-custom-1-3840"
}

variable "postgres_disk_size" {
  type    = number
  default = 50
}

variable "backend_bucket" {
  type = string
}
