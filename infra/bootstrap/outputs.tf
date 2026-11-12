# None of these are secrets: they go into repository *variables* (Settings > Secrets and
# variables > Actions > Variables), where the workflows read them as vars.<NAME>.

output "state_bucket" {
  description = "Terraform state bucket (repo variable TF_STATE_BUCKET)."
  value       = aws_s3_bucket.state.bucket
}

output "ci_plan_role_arn" {
  description = "Role for terraform plan on pull requests (repo variable AWS_PLAN_ROLE_ARN)."
  value       = aws_iam_role.ci_plan.arn
}

output "ci_apply_role_arn" {
  description = "Role for terraform apply in environment production (repo variable AWS_APPLY_ROLE_ARN)."
  value       = aws_iam_role.ci_apply.arn
}

output "ci_deploy_role_arn" {
  description = "Role for the SSM deploy in environment production (repo variable AWS_DEPLOY_ROLE_ARN)."
  value       = aws_iam_role.ci_deploy.arn
}

output "app_permissions_boundary_arn" {
  description = "Boundary every role in infra/app must carry."
  value       = aws_iam_policy.app_boundary.arn
}

output "github_oidc_provider_arn" {
  description = "IAM OIDC provider for token.actions.githubusercontent.com."
  value       = aws_iam_openid_connect_provider.github.arn
}
