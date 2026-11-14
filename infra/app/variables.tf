variable "region" {
  description = "AWS region."
  type        = string
  default     = "us-east-1"
}

variable "availability_zone" {
  description = "AZ of the subnet, the instance and the data volume. EBS volumes cannot leave their AZ, so changing this strands the data volume."
  type        = string
  default     = "us-east-1a"
}

variable "instance_type" {
  description = "EC2 instance type. t3.large (2 vCPU, 8 GiB) fits the API, call server, LiveKit, Postgres and the Grafana stack with the baked-in speech models."
  type        = string
  default     = "t3.large"
}

variable "root_volume_size_gb" {
  description = "Root disk: OS, Docker images (the app image carries its model weights) and container logs."
  type        = number
  default     = 30
}

variable "data_volume_size_gb" {
  description = "Separate EBS volume for Postgres, Grafana stack and Caddy data. Can grow in place, never shrink."
  type        = number
  default     = 20
}

variable "app_domain" {
  description = "Public hostname of the app (APP_DOMAIN)."
  type        = string
  default     = "tcc.gabrielfr.com"
}

variable "rtc_domain" {
  description = "LiveKit signaling hostname (RTC_DOMAIN)."
  type        = string
  default     = "rtc.tcc.gabrielfr.com"
}

variable "turn_domain" {
  description = "TURN/TLS hostname (TURN_DOMAIN)."
  type        = string
  default     = "turn.tcc.gabrielfr.com"
}

variable "grafana_domain" {
  description = "Grafana hostname (GRAFANA_DOMAIN)."
  type        = string
  default     = "grafana.tcc.gabrielfr.com"
}

variable "llm_provider" {
  description = "LLM_PROVIDER for the app (anthropic or openai, the latter for any OpenAI-protocol endpoint)."
  type        = string
  default     = "anthropic"

  validation {
    condition     = contains(["anthropic", "openai"], var.llm_provider)
    error_message = "llm_provider must be \"anthropic\" or \"openai\"."
  }
}

variable "llm_model" {
  description = "LLM_MODEL for the app."
  type        = string
  default     = "claude-sonnet-5-5"
}

variable "call_audio_retention_days" {
  description = "Days before call audio objects (tagged kind=call-audio) are deleted from the app bucket. Matches the app's transcript retention."
  type        = number
  default     = 180
}

variable "budget_limit_usd" {
  description = "Monthly cost budget in US$ that triggers the email alerts."
  type        = number
  default     = 80
}

variable "budget_email" {
  description = "Address the budget alerts go to. In CI pass it as TF_VAR_budget_email from a repository variable; it is not committed."
  type        = string
  default     = null
  nullable    = true
}
