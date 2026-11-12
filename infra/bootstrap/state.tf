# Remote state for both roots (keys bootstrap/ and app/). Versioned so a bad apply or a
# corrupted write can be rolled back to the previous state object. The lock is a sibling
# `<key>.tflock` object the S3 backend writes itself (use_lockfile), so there is no DynamoDB
# table to create or pay for.
#
# No server access logging (Trivy AVD-AWS-0089): it needs a second bucket to receive the
# logs, and the only principals that can reach this one are the admin profile and the
# CI roles, so the log would say nothing the IAM policies do not.
#trivy:ignore:AVD-AWS-0089
resource "aws_s3_bucket" "state" {
  bucket = var.state_bucket_name

  # Losing this bucket orphans every resource Terraform manages; refuse to plan deleting it.
  lifecycle {
    prevent_destroy = true
  }
}

resource "aws_s3_bucket_versioning" "state" {
  bucket = aws_s3_bucket.state.id
  versioning_configuration {
    status = "Enabled"
  }
}

# SSE-S3 rather than a customer-managed KMS key: the bucket is already reachable only by
# the admin profile and the CI roles below, and a CMK would add US$1/month plus key-policy
# upkeep without narrowing that audience (Trivy AVD-AWS-0132).
#trivy:ignore:AVD-AWS-0132
resource "aws_s3_bucket_server_side_encryption_configuration" "state" {
  bucket = aws_s3_bucket.state.id
  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm = "AES256"
    }
    bucket_key_enabled = true
  }
}

resource "aws_s3_bucket_public_access_block" "state" {
  bucket                  = aws_s3_bucket.state.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

# ACLs off: access is decided by IAM and the bucket policy alone.
resource "aws_s3_bucket_ownership_controls" "state" {
  bucket = aws_s3_bucket.state.id
  rule {
    object_ownership = "BucketOwnerEnforced"
  }
}

# Every apply writes a new state version; old ones only matter for a recent rollback.
resource "aws_s3_bucket_lifecycle_configuration" "state" {
  bucket = aws_s3_bucket.state.id

  rule {
    id     = "expire-old-state-versions"
    status = "Enabled"
    filter {}

    noncurrent_version_expiration {
      noncurrent_days           = 90
      newer_noncurrent_versions = 10
    }

    abort_incomplete_multipart_upload {
      days_after_initiation = 7
    }
  }

  depends_on = [aws_s3_bucket_versioning.state]
}

# State holds secrets (SSM SecureString values land in it on refresh), so plain HTTP is
# refused even for a caller that has the IAM permissions.
data "aws_iam_policy_document" "state_bucket" {
  statement {
    sid     = "DenyInsecureTransport"
    effect  = "Deny"
    actions = ["s3:*"]
    resources = [
      aws_s3_bucket.state.arn,
      "${aws_s3_bucket.state.arn}/*",
    ]
    principals {
      type        = "*"
      identifiers = ["*"]
    }
    condition {
      test     = "Bool"
      variable = "aws:SecureTransport"
      values   = ["false"]
    }
  }
}

resource "aws_s3_bucket_policy" "state" {
  bucket = aws_s3_bucket.state.id
  policy = data.aws_iam_policy_document.state_bucket.json

  # A policy write can race the public access block on a brand-new bucket.
  depends_on = [aws_s3_bucket_public_access_block.state]
}
