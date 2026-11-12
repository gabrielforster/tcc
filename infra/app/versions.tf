terraform {
  # 1.11: S3 backend native locking (`use_lockfile`) is GA.
  required_version = ">= 1.11, < 2.0"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 6.73"
    }
  }

  # The bucket is created by infra/bootstrap. Literal because backend blocks cannot read
  # variables; keep it equal to bootstrap's `state_bucket_name`. The CI roles can read and
  # write this key (and its .tflock) and nothing else in the bucket.
  backend "s3" {
    bucket       = "tcc-tfstate-gabrielforster"
    key          = "app/terraform.tfstate"
    region       = "us-east-1"
    encrypt      = true
    use_lockfile = true
  }
}
