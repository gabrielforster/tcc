# tcc-ci-apply: `terraform apply` of infra/app from master, through the `production`
# environment. It has to create and destroy real infrastructure, so the aim is not
# read-only but "only infra/app's resources", using whichever handle each service offers:
#
# - EC2:  tags. Creating a VPC, subnet, volume, instance... requires the request to tag it
#         Project=tcc (the provider's default_tags do that), and changing or deleting one
#         requires the existing resource to carry that tag. Untagged resources elsewhere in
#         the account are out of reach.
# - IAM:  name prefix tcc-app-* plus the permissions boundary (boundary.tf), so CI can
#         build the instance role but can never build an admin role.
# - S3, SSM, Budgets: name prefixes (tcc-app-* buckets, /tcc/* parameters, tcc-* budgets).
# - State: read/write the app key only; the bootstrap state is not readable from CI.
#
# Known gaps, accepted: Describe* is account-wide (EC2 has no resource-level read), and
# RunInstances' side resources (root volume, network interface, AMI) cannot be tag-gated
# the way the instance itself is.
resource "aws_iam_role" "ci_apply" {
  name                 = "tcc-ci-apply"
  description          = "GitHub Actions, environment production: terraform apply of infra/app."
  assume_role_policy   = data.aws_iam_policy_document.github_trust["apply"].json
  max_session_duration = 3600
}

locals {
  # Every EC2 create call infra/app makes. They all accept TagSpecifications, which is what
  # makes aws:RequestTag usable as the gate below.
  ec2_create_actions = [
    "ec2:AllocateAddress",
    "ec2:AuthorizeSecurityGroupEgress",
    "ec2:AuthorizeSecurityGroupIngress",
    "ec2:CreateInternetGateway",
    "ec2:CreateRouteTable",
    "ec2:CreateSecurityGroup",
    "ec2:CreateSubnet",
    "ec2:CreateVolume",
    "ec2:CreateVpc",
    "ec2:RunInstances",
  ]

  # Changes to resources that already exist, gated on the resource's own Project tag.
  ec2_tagged_actions = [
    "ec2:AssociateAddress",
    "ec2:AssociateRouteTable",
    "ec2:AttachInternetGateway",
    "ec2:AttachVolume",
    "ec2:AuthorizeSecurityGroupEgress",
    "ec2:AuthorizeSecurityGroupIngress",
    "ec2:CreateRoute",
    "ec2:DeleteInternetGateway",
    "ec2:DeleteRoute",
    "ec2:DeleteRouteTable",
    "ec2:DeleteSecurityGroup",
    "ec2:DeleteSubnet",
    "ec2:DeleteTags",
    "ec2:DeleteVolume",
    "ec2:DeleteVpc",
    "ec2:DetachInternetGateway",
    "ec2:DetachVolume",
    "ec2:DisassociateAddress",
    "ec2:DisassociateRouteTable",
    "ec2:ModifyInstanceAttribute",
    "ec2:ModifyInstanceCreditSpecification",
    "ec2:ModifyInstanceMetadataOptions",
    "ec2:ModifySecurityGroupRules",
    "ec2:ModifySubnetAttribute",
    "ec2:ModifyVolume",
    "ec2:ModifyVpcAttribute",
    "ec2:ReleaseAddress",
    "ec2:ReplaceRoute",
    "ec2:RevokeSecurityGroupEgress",
    "ec2:RevokeSecurityGroupIngress",
    "ec2:StartInstances",
    "ec2:StopInstances",
    "ec2:TerminateInstances",
    "ec2:UpdateSecurityGroupRuleDescriptionsEgress",
    "ec2:UpdateSecurityGroupRuleDescriptionsIngress",
  ]
}

data "aws_iam_policy_document" "ci_apply" {
  # --- state ----------------------------------------------------------------------------
  statement {
    sid       = "StateBucketList"
    actions   = ["s3:ListBucket"]
    resources = [aws_s3_bucket.state.arn]
  }

  statement {
    sid       = "AppStateReadWrite"
    actions   = ["s3:GetObject", "s3:PutObject"]
    resources = [local.app_state_object]
  }

  statement {
    sid       = "AppStateLock"
    actions   = ["s3:GetObject", "s3:PutObject", "s3:DeleteObject"]
    resources = [local.app_state_lockobj]
  }

  # --- EC2 --------------------------------------------------------------------------------
  statement {
    sid       = "Ec2Read"
    actions   = ["ec2:Describe*"]
    resources = ["*"]
  }

  # Creation must carry Project=tcc. For RunInstances this statement covers the instance
  # (the only RunInstances resource whose tags the provider always sends); the next one
  # covers the rest of that call.
  statement {
    sid       = "Ec2CreateTagged"
    actions   = local.ec2_create_actions
    resources = ["${local.ec2_arn_prefix}:*"]
    condition {
      test     = "StringEquals"
      variable = "aws:RequestTag/Project"
      values   = ["tcc"]
    }
  }

  # RunInstances also authorizes the AMI (owned by Canonical, so no account id), the subnet
  # and security group it lands in, and the root volume and network interface it creates
  # untagged. None can carry a tag condition reliably, but none can be created without the
  # tagged instance above, so this does not open anything on its own.
  statement {
    sid     = "Ec2RunInstancesSideResources"
    actions = ["ec2:RunInstances"]
    resources = [
      "arn:aws:ec2:${var.region}::image/*",
      "${local.ec2_arn_prefix}:subnet/*",
      "${local.ec2_arn_prefix}:security-group/*",
      "${local.ec2_arn_prefix}:network-interface/*",
      "${local.ec2_arn_prefix}:volume/*",
    ]
  }

  # Tagging is allowed while creating (CreateTags issued as part of a create call) and on
  # resources that are already the project's -- never to pull an outside resource in.
  statement {
    sid       = "Ec2TagOnCreate"
    actions   = ["ec2:CreateTags"]
    resources = ["${local.ec2_arn_prefix}:*"]
    condition {
      test     = "StringEquals"
      variable = "ec2:CreateAction"
      values   = local.ec2_create_actions
    }
  }

  statement {
    sid       = "Ec2ManageTagged"
    actions   = concat(local.ec2_tagged_actions, ["ec2:CreateTags"])
    resources = ["${local.ec2_arn_prefix}:*"]
    condition {
      test     = "StringEquals"
      variable = "aws:ResourceTag/Project"
      values   = ["tcc"]
    }
  }

  # The instance's network interface and individual security group rules are authorized
  # alongside the tagged instance / group, but are not themselves tagged by the provider.
  statement {
    sid = "Ec2UntaggedSubResources"
    actions = [
      "ec2:AssociateAddress",
      "ec2:DisassociateAddress",
      "ec2:AuthorizeSecurityGroupEgress",
      "ec2:AuthorizeSecurityGroupIngress",
      "ec2:ModifySecurityGroupRules",
      "ec2:RevokeSecurityGroupEgress",
      "ec2:RevokeSecurityGroupIngress",
    ]
    resources = [
      "${local.ec2_arn_prefix}:network-interface/*",
      "${local.ec2_arn_prefix}:security-group-rule/*",
    ]
  }

  # --- IAM (instance role and profile) ---------------------------------------------------
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

  # A tcc-app-* role can only be created, or have policies put on it, if it carries the
  # boundary; with that, whatever CI attaches cannot exceed boundary.tf.
  statement {
    sid = "IamRoleWithBoundary"
    actions = [
      "iam:CreateRole",
      "iam:PutRolePermissionsBoundary",
      "iam:PutRolePolicy",
    ]
    resources = [local.app_role_arn]
    condition {
      test     = "StringEquals"
      variable = "iam:PermissionsBoundary"
      values   = [aws_iam_policy.app_boundary.arn]
    }
  }

  # Managed policies: only the SSM agent's, and only onto a bounded role.
  statement {
    sid       = "IamAttachSsmCore"
    actions   = ["iam:AttachRolePolicy", "iam:DetachRolePolicy"]
    resources = [local.app_role_arn]
    condition {
      test     = "ArnEquals"
      variable = "iam:PolicyARN"
      values   = [local.ssm_managed_policy_arn]
    }
    condition {
      test     = "StringEquals"
      variable = "iam:PermissionsBoundary"
      values   = [aws_iam_policy.app_boundary.arn]
    }
  }

  # Removing things and editing metadata do not widen anything. There is deliberately no
  # iam:DeleteRolePermissionsBoundary: a role loses its ceiling only by being deleted.
  statement {
    sid = "IamRoleMaintain"
    actions = [
      "iam:DeleteRole",
      "iam:DeleteRolePolicy",
      "iam:TagRole",
      "iam:UntagRole",
      "iam:UpdateAssumeRolePolicy",
      "iam:UpdateRole",
      "iam:UpdateRoleDescription",
    ]
    resources = [local.app_role_arn]
  }

  statement {
    sid = "IamInstanceProfile"
    actions = [
      "iam:AddRoleToInstanceProfile",
      "iam:CreateInstanceProfile",
      "iam:DeleteInstanceProfile",
      "iam:RemoveRoleFromInstanceProfile",
      "iam:TagInstanceProfile",
      "iam:UntagInstanceProfile",
    ]
    resources = [local.app_profile_arn]
  }

  # Needed to launch the instance with its profile; only to EC2, only tcc-app-* roles.
  statement {
    sid       = "IamPassInstanceRole"
    actions   = ["iam:PassRole"]
    resources = [local.app_role_arn]
    condition {
      test     = "StringEquals"
      variable = "iam:PassedToService"
      values   = ["ec2.amazonaws.com"]
    }
  }

  # --- S3 (app bucket configuration, not its objects) ------------------------------------
  # Bucket ARN only: every bucket-level call (create, delete, versioning, encryption,
  # lifecycle, public access block, policy, tags) but no object read or write -- CI has no
  # business with the app's data.
  statement {
    sid = "AppBucketManage"
    actions = [
      "s3:CreateBucket",
      "s3:DeleteBucket",
      "s3:DeleteBucketPolicy",
      "s3:Get*",
      "s3:List*",
      "s3:Put*",
    ]
    resources = [local.app_bucket_arn]
  }

  # --- SSM parameters ---------------------------------------------------------------------
  statement {
    sid       = "ParametersList"
    actions   = ["ssm:DescribeParameters"]
    resources = ["*"]
  }

  statement {
    sid = "ParametersManage"
    actions = [
      "ssm:AddTagsToResource",
      "ssm:DeleteParameter",
      "ssm:DeleteParameters",
      "ssm:GetParameter",
      "ssm:GetParameters",
      "ssm:ListTagsForResource",
      "ssm:PutParameter",
      "ssm:RemoveTagsFromResource",
    ]
    resources = [local.ssm_param_arn]
  }

  # Spelled out for clarity and for a future customer-managed key: with the default
  # aws/ssm key the key policy already admits account principals calling through SSM.
  statement {
    sid       = "SecureStringsViaSsm"
    actions   = ["kms:Decrypt", "kms:Encrypt", "kms:GenerateDataKey"]
    resources = ["arn:aws:kms:${var.region}:${local.account_id}:key/*"]
    condition {
      test     = "StringEquals"
      variable = "kms:ViaService"
      values   = ["ssm.${var.region}.amazonaws.com"]
    }
  }

  # --- Budgets ----------------------------------------------------------------------------
  # Creating a budget is budgets:ModifyBudget in IAM.
  statement {
    sid = "BudgetManage"
    actions = [
      "budgets:ListTagsForResource",
      "budgets:ModifyBudget",
      "budgets:TagResource",
      "budgets:UntagResource",
      "budgets:ViewBudget",
    ]
    resources = [local.budget_arn]
  }
}

resource "aws_iam_role_policy" "ci_apply" {
  name   = "terraform-apply-app"
  role   = aws_iam_role.ci_apply.id
  policy = data.aws_iam_policy_document.ci_apply.json
}
