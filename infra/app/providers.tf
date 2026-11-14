provider "aws" {
  region = var.region

  # Not just bookkeeping: tcc-ci-apply may only create EC2 resources whose create request
  # carries Project=tcc, and may only change or delete ones that already carry it
  # (infra/bootstrap/iam_apply.tf). default_tags is what puts the tag on every request.
  default_tags {
    tags = {
      Project     = "tcc"
      Environment = "prod"
      ManagedBy   = "terraform"
    }
  }
}

data "aws_caller_identity" "current" {}
