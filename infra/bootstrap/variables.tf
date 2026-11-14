variable "region" {
  description = "AWS region for everything in this project."
  type        = string
  default     = "us-east-1"
}

variable "github_repository" {
  description = "owner/name of the GitHub repository whose workflows may assume the CI roles."
  type        = string
  default     = "gabrielforster/tcc"
}

variable "github_environment" {
  description = "GitHub environment that gates the apply and deploy roles (restricted to master in the repo settings)."
  type        = string
  default     = "production"
}

variable "state_bucket_name" {
  description = "Terraform state bucket. Must equal the literal bucket in the backend blocks of both roots."
  type        = string
  default     = "tcc-tfstate-gabrielforster"
}
