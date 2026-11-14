provider "aws" {
  region = var.region

  default_tags {
    tags = {
      Project     = "tcc"
      Environment = "prod"
      ManagedBy   = "terraform"
    }
  }
}

data "aws_caller_identity" "current" {}
