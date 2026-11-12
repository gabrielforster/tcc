# App object storage in prod (MinIO plays this part locally): raw/interim frames, trained
# models and metrics, plots, and call audio if it is ever persisted. The account id in the
# name makes it globally unique without a random suffix, so the name is predictable.
#
# No server access logging (Trivy AVD-AWS-0089): it needs a second bucket for the logs,
# and the only reader/writer is the instance role, whose activity the app's own
# traces and logs (Grafana stack) already cover.
#trivy:ignore:AVD-AWS-0089
resource "aws_s3_bucket" "app" {
  bucket = "tcc-app-${data.aws_caller_identity.current.account_id}"
}

resource "aws_s3_bucket_versioning" "app" {
  bucket = aws_s3_bucket.app.id
  versioning_configuration {
    status = "Enabled"
  }
}

# SSE-S3: the instance role is the only reader, so a customer-managed key would add cost
# without narrowing who can read (Trivy AVD-AWS-0132).
#trivy:ignore:AVD-AWS-0132
resource "aws_s3_bucket_server_side_encryption_configuration" "app" {
  bucket = aws_s3_bucket.app.id
  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm = "AES256"
    }
    bucket_key_enabled = true
  }
}

resource "aws_s3_bucket_public_access_block" "app" {
  bucket                  = aws_s3_bucket.app.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_s3_bucket_ownership_controls" "app" {
  bucket = aws_s3_bucket.app.id
  rule {
    object_ownership = "BucketOwnerEnforced"
  }
}

resource "aws_s3_bucket_lifecycle_configuration" "app" {
  bucket = aws_s3_bucket.app.id

  # Versioning is there to undo a bad overwrite, not to archive; a re-trained model or a
  # re-ingested frame otherwise keeps every old copy forever.
  rule {
    id     = "expire-old-versions"
    status = "Enabled"
    filter {}

    noncurrent_version_expiration {
      noncurrent_days = 30
    }

    abort_incomplete_multipart_upload {
      days_after_initiation = 7
    }
  }

  # Call audio is personal data (LGPD) and is kept no longer than its transcript. Keys are
  # tenants/<id>/<kind>/<name>, and a lifecycle prefix cannot wildcard the tenant id, so the
  # rule matches an object tag instead: the uploader must tag audio `kind=call-audio`.
  rule {
    id     = "expire-call-audio"
    status = "Enabled"
    filter {
      tag {
        key   = "kind"
        value = "call-audio"
      }
    }

    expiration {
      days = var.call_audio_retention_days
    }

    noncurrent_version_expiration {
      noncurrent_days = 1
    }
  }

  depends_on = [aws_s3_bucket_versioning.app]
}

data "aws_iam_policy_document" "app_bucket" {
  statement {
    sid     = "DenyInsecureTransport"
    effect  = "Deny"
    actions = ["s3:*"]
    resources = [
      aws_s3_bucket.app.arn,
      "${aws_s3_bucket.app.arn}/*",
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

resource "aws_s3_bucket_policy" "app" {
  bucket = aws_s3_bucket.app.id
  policy = data.aws_iam_policy_document.app_bucket.json

  depends_on = [aws_s3_bucket_public_access_block.app]
}
