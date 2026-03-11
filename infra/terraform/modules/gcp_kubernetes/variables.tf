variable "project" {
  type = string
}

variable "region" {
  type = string
}

variable "cluster_name" {
  type = string
}

variable "node_count" {
  type    = number
  default = 2
}

variable "node_vm_type" {
  type    = string
  default = "e2-standard-2"
}

variable "network" {
  type = string
}

variable "subnetwork" {
  type = string
}
