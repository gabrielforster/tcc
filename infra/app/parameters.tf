# Runtime configuration under /tcc/prod/<ENV_VAR_NAME>. deploy.sh renders every parameter
# under the prefix into /opt/tcc/.env, so adding a setting to prod is adding a parameter
# here (or by hand, for a secret) -- the deploy script never changes.

locals {
  # Non-secret, owned by Terraform: a change here is a reviewed diff in a PR plan.
  plain_parameters = {
    AWS_REGION     = var.region
    S3_BUCKET      = aws_s3_bucket.app.bucket
    APP_DOMAIN     = var.app_domain
    RTC_DOMAIN     = var.rtc_domain
    TURN_DOMAIN    = var.turn_domain
    GRAFANA_DOMAIN = var.grafana_domain
    LLM_PROVIDER   = var.llm_provider
    LLM_MODEL      = var.llm_model
    DATA_SOURCE    = "synthetic"
  }

  # Secrets: Terraform only creates the name; Gabriel sets the value out of band with
  # `aws ssm put-parameter --overwrite`. Their values never appear in HCL or a plan.
  secret_parameters = toset([
    "ANTHROPIC_API_KEY",
    "LLM_API_KEY",
    "ANONYMIZATION_SALT",
    "LIVEKIT_API_KEY",
    "LIVEKIT_API_SECRET",
    "POSTGRES_PASSWORD",
    "GRAFANA_ADMIN_PASSWORD",
    "GRAFANA_VIEWER_PASSWORD",
  ])
}

resource "aws_ssm_parameter" "plain" {
  for_each = local.plain_parameters

  name = "${local.param_prefix}/${each.key}"
  type = "String"
  # insecure_value, not value: these are not secrets, and keeping them visible makes a
  # change readable in the PR's plan instead of "(sensitive value)".
  insecure_value = each.value
}

resource "aws_ssm_parameter" "secret" {
  for_each = local.secret_parameters

  name  = "${local.param_prefix}/${each.key}"
  type  = "SecureString" # encrypted with the AWS-managed aws/ssm key
  # deploy.sh knows this exact value: it drops the optional LLM keys still holding it and
  # refuses to deploy while any other secret does.
  value = "placeholder-set-me-with-aws-ssm-put-parameter"

  # The real value is set by hand after the first apply; without this, the next apply would
  # write the placeholder back over it. Caveat: the provider still reads the current value
  # on refresh, so it does land in the (encrypted, access-restricted) state object.
  lifecycle {
    ignore_changes = [value]
  }
}
