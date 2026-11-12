terraform {
  # 1.11 is the first release where the S3 backend's native lockfile (`use_lockfile`) is
  # generally available, which is what lets this project skip a DynamoDB lock table.
  required_version = ">= 1.11, < 2.0"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 6.73"
    }
  }

  # This backend points at the bucket this very root creates, so it cannot be used for the
  # first apply: that one runs with a local backend override and is then migrated here with
  # `terraform init -migrate-state` (README.md walks through it). Backend blocks cannot read
  # variables, hence the literal bucket name; it must match `var.state_bucket_name`.
  backend "s3" {
    bucket       = "tcc-tfstate-gabrielforster"
    key          = "bootstrap/terraform.tfstate"
    region       = "us-east-1"
    encrypt      = true
    use_lockfile = true
  }
}
