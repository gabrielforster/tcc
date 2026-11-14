locals {
  account_id = data.aws_caller_identity.current.account_id

  # Everything infra/app names starts with this prefix. The CI roles' IAM and S3 permissions
  # are scoped to it, so a resource outside it is out of CI's reach even if the HCL asks
  # for it -- a reviewer does not have to catch that in a plan.
  app_prefix = "tcc-app"

  app_bucket_arn  = "arn:aws:s3:::${local.app_prefix}-*"
  app_role_arn    = "arn:aws:iam::${local.account_id}:role/${local.app_prefix}-*"
  app_profile_arn = "arn:aws:iam::${local.account_id}:instance-profile/${local.app_prefix}-*"
  ssm_param_arn   = "arn:aws:ssm:${var.region}:${local.account_id}:parameter/tcc/*"
  budget_arn      = "arn:aws:budgets::${local.account_id}:budget/tcc-*"
  ec2_arn_prefix  = "arn:aws:ec2:${var.region}:${local.account_id}"

  ssm_managed_policy_arn = "arn:aws:iam::aws:policy/AmazonSSMManagedInstanceCore"

  app_state_object  = "${aws_s3_bucket.state.arn}/app/terraform.tfstate"
  app_state_lockobj = "${aws_s3_bucket.state.arn}/app/terraform.tfstate.tflock"

  oidc_host = "token.actions.githubusercontent.com"
}
