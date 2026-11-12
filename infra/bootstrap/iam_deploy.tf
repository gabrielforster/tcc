# tcc-ci-deploy: ship a release without SSH. The deploy job runs one shell script on the
# instance through SSM Run Command and polls for its result; that is all this role can do.
#
# ssm:SendCommand is authorized against both the document and the target instance, so the
# two statements together mean "only AWS-RunShellScript, only on instances tagged
# Project=tcc". A command against any other instance, or with any other document (e.g.
# one that changes IAM or installs software through a different path), is denied.
resource "aws_iam_role" "ci_deploy" {
  name                 = "tcc-ci-deploy"
  description          = "GitHub Actions, environment production: run deploy.sh on the tcc instance via SSM."
  assume_role_policy   = data.aws_iam_policy_document.github_trust["deploy"].json
  max_session_duration = 3600
}

data "aws_iam_policy_document" "ci_deploy" {
  statement {
    sid       = "RunShellScriptDocument"
    actions   = ["ssm:SendCommand"]
    resources = ["arn:aws:ssm:${var.region}::document/AWS-RunShellScript"]
  }

  statement {
    sid       = "OnlyProjectInstances"
    actions   = ["ssm:SendCommand"]
    resources = ["${local.ec2_arn_prefix}:instance/*"]
    condition {
      test     = "StringEquals"
      variable = "ssm:resourceTag/Project"
      values   = ["tcc"]
    }
  }

  # Reading results has no resource-level permissions in SSM. These calls only return
  # command output and status, never run anything.
  statement {
    sid = "CommandResults"
    actions = [
      "ssm:GetCommandInvocation",
      "ssm:ListCommandInvocations",
      "ssm:ListCommands",
    ]
    resources = ["*"]
  }

  # Lets the job resolve "the instance tagged Name=tcc-prod" to an id instead of the id
  # being copied into a repo variable that goes stale when the instance is replaced.
  statement {
    sid       = "FindInstance"
    actions   = ["ec2:DescribeInstances"]
    resources = ["*"]
  }
}

resource "aws_iam_role_policy" "ci_deploy" {
  name   = "ssm-run-deploy"
  role   = aws_iam_role.ci_deploy.id
  policy = data.aws_iam_policy_document.ci_deploy.json
}
