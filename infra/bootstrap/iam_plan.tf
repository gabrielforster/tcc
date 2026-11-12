# tcc-ci-plan: what `terraform plan` on a pull request needs, and nothing that changes the
# account. A custom read-only policy instead of the managed ReadOnlyAccess: that one reads
# every bucket's objects and every parameter in the account, while a plan of infra/app only
# has to refresh what infra/app manages.
resource "aws_iam_role" "ci_plan" {
  name                 = "tcc-ci-plan"
  description          = "GitHub Actions, pull requests: terraform plan of infra/app (read-only)."
  assume_role_policy   = data.aws_iam_policy_document.github_trust["plan"].json
  max_session_duration = 3600
}

data "aws_iam_policy_document" "ci_plan" {
  # --- state ----------------------------------------------------------------------------
  statement {
    sid       = "StateBucketList"
    actions   = ["s3:ListBucket"]
    resources = [aws_s3_bucket.state.arn]
  }

  statement {
    sid       = "AppStateRead"
    actions   = ["s3:GetObject"]
    resources = [local.app_state_object]
  }

  # `plan` takes the state lock like any other command. The only write this role has is the
  # lock object; the alternative (`plan -lock=false`) would let a PR plan read a state that
  # an apply is halfway through writing.
  statement {
    sid       = "AppStateLock"
    actions   = ["s3:GetObject", "s3:PutObject", "s3:DeleteObject"]
    resources = [local.app_state_lockobj]
  }

  # --- refresh of what infra/app manages -------------------------------------------------
  # Describe* has no resource-level permissions in EC2, hence "*". It is metadata only (no
  # console output or password data, which are ec2:Get*).
  statement {
    sid       = "Ec2Read"
    actions   = ["ec2:Describe*"]
    resources = ["*"]
  }

  statement {
    sid     = "IamRead"
    actions = ["iam:Get*", "iam:List*"]
    resources = [
      local.app_role_arn,
      local.app_profile_arn,
      aws_iam_policy.app_boundary.arn,
      local.ssm_managed_policy_arn,
    ]
  }

  # Bucket ARN only, no `/*`: s3:Get* here reads the bucket's configuration (encryption,
  # lifecycle, versioning...) but not a single object -- GetObject needs an object ARN.
  statement {
    sid       = "AppBucketConfigRead"
    actions   = ["s3:Get*", "s3:ListBucket"]
    resources = [local.app_bucket_arn]
  }

  statement {
    sid       = "ParametersList"
    actions   = ["ssm:DescribeParameters"]
    resources = ["*"]
  }

  # The AWS provider always reads SecureStrings decrypted, so refreshing them needs Decrypt.
  # That is a real exposure (a PR job sees secret values), accepted because only branches
  # of this single-maintainer repo get a token, and the same values are in the state the
  # role must read anyway.
  statement {
    sid       = "ParametersRead"
    actions   = ["ssm:GetParameter", "ssm:GetParameters", "ssm:ListTagsForResource"]
    resources = [local.ssm_param_arn]
  }

  statement {
    sid       = "DecryptSecureStringsViaSsm"
    actions   = ["kms:Decrypt"]
    resources = ["arn:aws:kms:${var.region}:${local.account_id}:key/*"]
    condition {
      test     = "StringEquals"
      variable = "kms:ViaService"
      values   = ["ssm.${var.region}.amazonaws.com"]
    }
  }

  statement {
    sid       = "BudgetRead"
    actions   = ["budgets:ViewBudget", "budgets:ListTagsForResource"]
    resources = [local.budget_arn]
  }
}

resource "aws_iam_role_policy" "ci_plan" {
  name   = "terraform-plan-app"
  role   = aws_iam_role.ci_plan.id
  policy = data.aws_iam_policy_document.ci_plan.json
}
