resource "google_compute_network" "missioncontrol" {
  name                    = var.network_name
  auto_create_subnetworks = false
  routing_mode            = "GLOBAL"
  project                 = var.project
}

resource "google_compute_subnetwork" "missioncontrol" {
  name          = var.subnetwork_name
  ip_cidr_range = var.subnetwork_cidr
  network       = google_compute_network.missioncontrol.id
  region        = var.region
  project       = var.project
}
