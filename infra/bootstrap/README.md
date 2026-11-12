# infra/bootstrap

The part of the AWS setup that CI cannot create for itself: the bucket that holds Terraform
state, the GitHub OIDC provider, and the three roles GitHub Actions assumes. It is applied
**by hand, once**, from a laptop with an admin profile, and re-applied only when one of these
changes. Everything else (network, instance, bucket, parameters, budget) is `infra/app`, which
CI applies.

| Resource | Why |
|---|---|
| S3 bucket `tcc-tfstate-gabrielforster` | State for both roots (`bootstrap/terraform.tfstate`, `app/terraform.tfstate`). Versioned, SSE-S3, public access blocked, TLS-only policy; locking is the backend's native `.tflock` object (`use_lockfile`), so no DynamoDB table. `prevent_destroy`. |
| IAM OIDC provider `token.actions.githubusercontent.com` | GitHub jobs exchange their OIDC token for short-lived credentials. No AWS keys in GitHub. |
| Role `tcc-ci-plan` | Trusts `repo:gabrielforster/tcc:pull_request`. Read-only refresh of what `infra/app` manages, plus read of the app state (and its lock object). |
| Role `tcc-ci-apply` | Trusts `repo:gabrielforster/tcc:environment:production`. Creates/changes/destroys only `infra/app` resources: EC2 gated by the `Project=tcc` tag, IAM by the `tcc-app-` prefix **and** a permissions boundary, S3/SSM/Budgets by name prefix. |
| Role `tcc-ci-deploy` | Trusts the same environment. Only `ssm:SendCommand` with `AWS-RunShellScript` to instances tagged `Project=tcc`, and reading the command's result. |
| Policy `tcc-permissions-boundary` | Ceiling for every role `infra/app` creates. Without it, "CI may create roles" would mean "CI may create an admin role". Outside the `tcc-app-` prefix so CI cannot edit it. |

## First apply (one time, local state)

The S3 backend in `versions.tf` points at a bucket this root creates, so the first apply has
to keep its state locally and move it into the bucket afterwards.

1. Confirm the profile points at the right account and has admin rights:

   ```sh
   export AWS_PROFILE=default
   aws sts get-caller-identity
   ```

2. Override the backend with a local one. `*_override.tf` files are merged over the real
   configuration by Terraform and are git-ignored, so this never gets committed:

   ```sh
   cd infra/bootstrap
   printf 'terraform {\n  backend "local" {}\n}\n' > backend_override.tf
   terraform init
   terraform apply
   ```

3. Move the state into the bucket and drop the override:

   ```sh
   rm backend_override.tf
   terraform init -migrate-state      # answer "yes" to copy local state to S3
   rm -f terraform.tfstate terraform.tfstate.backup
   terraform plan                     # must report "No changes"
   ```

   From here on the state lives at `s3://tcc-tfstate-gabrielforster/bootstrap/terraform.tfstate`
   and later applies are a plain `terraform apply` with the same profile.

## After the apply

1. **Repository variables** (Settings → Secrets and variables → Actions → *Variables*, not
   secrets — none of these is secret). Values from `terraform output`:

   | Variable | Output |
   |---|---|
   | `AWS_PLAN_ROLE_ARN` | `ci_plan_role_arn` |
   | `AWS_APPLY_ROLE_ARN` | `ci_apply_role_arn` |
   | `AWS_DEPLOY_ROLE_ARN` | `ci_deploy_role_arn` |
   | `TF_STATE_BUCKET` | `state_bucket` |

2. **GitHub environment `production`** (Settings → Environments → New environment), with
   *Deployment branches and tags* set to **Selected branches: `master`**. The apply and deploy
   roles trust any job that runs in this environment, so this branch rule is what keeps a
   feature branch from applying or deploying.

3. Jobs that use OIDC need `permissions: id-token: write`. The plan job must **not** declare an
   environment (its token subject would then be `environment:production`, not `pull_request`).

## Changing the bucket name

S3 bucket names are global. If `tcc-tfstate-gabrielforster` is taken, set
`state_bucket_name` and change the literal `bucket` in **both** `infra/bootstrap/versions.tf`
and `infra/app/versions.tf` — backend blocks cannot read variables.
