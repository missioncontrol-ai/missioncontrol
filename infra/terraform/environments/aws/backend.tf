terraform {
  backend "s3" {
    bucket         = var.backend_bucket
    key            = "missioncontrol/aws.tfstate"
    region         = var.region
    dynamodb_table = var.backend_dynamodb
  }
}
