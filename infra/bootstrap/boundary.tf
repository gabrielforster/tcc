# Permissions boundary for every role infra/app creates (today: the EC2 instance role).
#
# Why it exists: tcc-ci-apply must be able to create the instance role and give it
# policies, and "may create roles and attach policies" is, unbounded, a path to admin --
# CI could mint a role with AdministratorAccess and hand it to an instance. The apply role
# may only create roles that carry THIS boundary (iam_apply.tf), and a boundary is a
# ceiling: whatever is attached to such a role, it can never do more than what is listed
# here. It lives in bootstrap, outside CI's write reach, so CI cannot raise its own ceiling.
#
# The ceiling is exactly what the instance needs: the app bucket, the /tcc/* parameters,
# and what the SSM agent needs to register and run commands (AmazonSSMManagedInstanceCore,
# minus its account-wide ssm:GetParameter*).
data "aws_iam_policy_document" "app_boundary" {
  statement {
    sid       = "AppBucketList"
    actions   = ["s3:ListBucket", "s3:GetBucketLocation"]
    resources = [local.app_bucket_arn]
  }

  statement {
    sid       = "AppBucketObjects"
    actions   = ["s3:GetObject", "s3:PutObject", "s3:DeleteObject"]
    resources = ["${local.app_bucket_arn}/*"]
  }

  statement {
    sid = "ProjectParameters"
    actions = [
      "ssm:GetParameter",
      "ssm:GetParameters",
      "ssm:GetParametersByPath",
    ]
    resources = [
      "arn:aws:ssm:${var.region}:${local.account_id}:parameter/tcc",
      local.ssm_param_arn,
    ]
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
    sid = "SsmAgent"
    actions = [
      "ssm:DescribeAssociation",
      "ssm:DescribeDocument",
      "ssm:GetDeployablePatchSnapshotForInstance",
      "ssm:GetDocument",
      "ssm:GetManifest",
      "ssm:ListAssociations",
      "ssm:ListInstanceAssociations",
      "ssm:PutComplianceItems",
      "ssm:PutConfigurePackageResult",
      "ssm:PutInventory",
      "ssm:UpdateAssociationStatus",
      "ssm:UpdateInstanceAssociationStatus",
      "ssm:UpdateInstanceInformation",
      "ssmmessages:CreateControlChannel",
      "ssmmessages:CreateDataChannel",
      "ssmmessages:OpenControlChannel",
      "ssmmessages:OpenDataChannel",
      "ec2messages:AcknowledgeMessage",
      "ec2messages:DeleteMessage",
      "ec2messages:FailMessage",
      "ec2messages:GetEndpoint",
      "ec2messages:GetMessages",
      "ec2messages:SendReply",
    ]
    resources = ["*"]
  }
}

resource "aws_iam_policy" "app_boundary" {
  # Deliberately NOT under the tcc-app- prefix: the apply role manages tcc-app-* IAM
  # objects, and must not be able to touch its own ceiling.
  name        = "tcc-permissions-boundary"
  description = "Ceiling for roles created by infra/app (tcc-ci-apply may only create roles carrying it)."
  policy      = data.aws_iam_policy_document.app_boundary.json
}
