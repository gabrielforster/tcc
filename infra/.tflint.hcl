# Shared by both roots: `tflint --chdir infra/<root> --config "$PWD/infra/.tflint.hcl"`.
# The bundled Terraform ruleset only (naming, unused declarations, pinned versions,
# documented variables/outputs); no cloud plugin, so linting needs no download and no
# credentials.
config {
  call_module_type = "none"
}

plugin "terraform" {
  enabled = true
  preset  = "all"
}

# The rule is written for reusable modules (main.tf as the entrypoint a consumer opens).
# These are root modules split one file per concern (network.tf, compute.tf, iam_*.tf...),
# which reads better than one main.tf holding everything.
rule "terraform_standard_module_structure" {
  enabled = false
}
