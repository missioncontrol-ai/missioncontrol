terraform {
  backend "gcs" {
    bucket = var.backend_bucket
    prefix = "missioncontrol/gcp"
  }
}
