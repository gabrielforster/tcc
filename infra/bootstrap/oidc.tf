# Lets GitHub Actions trade its per-job OIDC token for short-lived AWS credentials, so no
# AWS access key is ever stored in GitHub. No thumbprint: for this issuer AWS validates
# GitHub's certificate against its own trusted CAs, and the provider no longer needs one.
resource "aws_iam_openid_connect_provider" "github" {
  url            = "https://${local.oidc_host}"
  client_id_list = ["sts.amazonaws.com"]
}

# One trust policy per role, each pinned to one exact `sub` claim. StringEquals rather than
# StringLike on purpose: a wildcard would let any branch, tag or environment of the repo in.
#
# - plan:  `pull_request` is the subject GitHub issues to a job triggered by a PR that does
#          not name an environment. Fork PRs get no OIDC token at all, so only branches
#          pushed to this repo can plan.
# - apply/deploy: the subject of a job that declares `environment: production`. The
#          environment's deployment-branch rule (master only) is what keeps a feature branch
#          from asking for it, so that rule in the repo settings is part of this boundary.
data "aws_iam_policy_document" "github_trust" {
  for_each = {
    plan   = "repo:${var.github_repository}:pull_request"
    apply  = "repo:${var.github_repository}:environment:${var.github_environment}"
    deploy = "repo:${var.github_repository}:environment:${var.github_environment}"
  }

  statement {
    actions = ["sts:AssumeRoleWithWebIdentity"]
    principals {
      type        = "Federated"
      identifiers = [aws_iam_openid_connect_provider.github.arn]
    }
    condition {
      test     = "StringEquals"
      variable = "${local.oidc_host}:aud"
      values   = ["sts.amazonaws.com"]
    }
    condition {
      test     = "StringEquals"
      variable = "${local.oidc_host}:sub"
      values   = [each.value]
    }
  }
}
