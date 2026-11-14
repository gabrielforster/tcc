# The instance's identity. The containers reach S3 and the deploy script reads SSM with
# it, through IMDSv2, so the box holds no long-lived keys (S3_ACCESS_KEY/S3_SECRET_KEY stay
# unset in prod and boto3 falls back to the instance role).
#
# Named tcc-app-* and carrying the bootstrap boundary because tcc-ci-apply may create no
# other kind of role (infra/bootstrap/iam_apply.tf, boundary.tf).

locals {
  permissions_boundary_arn = "arn:aws:iam::${data.aws_caller_identity.current.account_id}:policy/tcc-permissions-boundary"
  param_prefix             = "/tcc/prod"
}

data "aws_iam_policy_document" "instance_trust" {
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["ec2.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "instance" {
  name                 = "tcc-app-instance"
  description          = "tcc prod EC2 instance: app bucket, /tcc/prod parameters, SSM agent."
  assume_role_policy   = data.aws_iam_policy_document.instance_trust.json
  permissions_boundary = local.permissions_boundary_arn
}

# Session Manager and Run Command: the only way in, since port 22 is closed.
resource "aws_iam_role_policy_attachment" "ssm_core" {
  role       = aws_iam_role.instance.name
  policy_arn = "arn:aws:iam::aws:policy/AmazonSSMManagedInstanceCore"
}

data "aws_iam_policy_document" "instance" {
  # HeadBucket (the app checks the bucket on first use) is authorized as ListBucket.
  statement {
    sid       = "AppBucket"
    actions   = ["s3:ListBucket", "s3:GetBucketLocation"]
    resources = [aws_s3_bucket.app.arn]
  }

  statement {
    sid       = "AppObjects"
    actions   = ["s3:GetObject", "s3:PutObject", "s3:DeleteObject"]
    resources = ["${aws_s3_bucket.app.arn}/*"]
  }

  # deploy.sh renders every parameter under the prefix into /opt/tcc/.env with
  # `get-parameters-by-path --recursive`; GetParametersByPath is authorized on the path
  # itself, the single-parameter calls on each parameter.
  statement {
    sid     = "ProdParameters"
    actions = ["ssm:GetParametersByPath", "ssm:GetParameter", "ssm:GetParameters"]
    resources = [
      "arn:aws:ssm:${var.region}:${data.aws_caller_identity.current.account_id}:parameter${local.param_prefix}",
      "arn:aws:ssm:${var.region}:${data.aws_caller_identity.current.account_id}:parameter${local.param_prefix}/*",
    ]
  }

  # SecureStrings use the AWS-managed aws/ssm key. Its key policy already admits account
  # principals calling through SSM; stating it here keeps the role self-describing and
  # keeps working if the parameters move to a customer-managed key.
  statement {
    sid       = "DecryptSecureStrings"
    actions   = ["kms:Decrypt"]
    resources = ["arn:aws:kms:${var.region}:${data.aws_caller_identity.current.account_id}:key/*"]
    condition {
      test     = "StringEquals"
      variable = "kms:ViaService"
      values   = ["ssm.${var.region}.amazonaws.com"]
    }
  }
}

resource "aws_iam_role_policy" "instance" {
  name   = "tcc-app-instance"
  role   = aws_iam_role.instance.id
  policy = data.aws_iam_policy_document.instance.json
}

resource "aws_iam_instance_profile" "instance" {
  name = "tcc-app-instance"
  role = aws_iam_role.instance.name
}
