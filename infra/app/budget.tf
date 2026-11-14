# Account-wide monthly cost budget, not filtered by the Project tag: the point is to catch
# anything that costs money in this account, including something created outside Terraform.
# Expected spend is ~US$68/month (README.md), so the default US$80 leaves headroom for
# traffic and still fires on a forgotten resource or an instance-type mistake.
resource "aws_budgets_budget" "monthly" {
  name         = "tcc-monthly"
  budget_type  = "COST"
  limit_amount = format("%.2f", var.budget_limit_usd)
  limit_unit   = "USD"
  time_unit    = "MONTHLY"

  dynamic "notification" {
    for_each = var.budget_email == null ? {} : {
      actual_80    = { type = "ACTUAL", threshold = 80 }
      actual_100   = { type = "ACTUAL", threshold = 100 }
      forecast_100 = { type = "FORECASTED", threshold = 100 }
    }

    content {
      comparison_operator        = "GREATER_THAN"
      notification_type          = notification.value.type
      threshold                  = notification.value.threshold
      threshold_type             = "PERCENTAGE"
      subscriber_email_addresses = [var.budget_email]
    }
  }
}

# Without an address the budget still tracks spend but alerts nobody; say so on every plan
# rather than fail it, so a missing CI variable does not block an unrelated apply.
check "budget_alerts_have_a_recipient" {
  assert {
    condition     = var.budget_email != null
    error_message = "budget_email is not set: the tcc-monthly budget exists but sends no alerts. Set TF_VAR_budget_email (repository variable BUDGET_EMAIL in CI)."
  }
}
