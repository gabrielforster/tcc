# infra/app

Production for the tcc app: one EC2 instance running the Docker Compose bundle from
`deploy/`, with its data on a separate EBS volume. State lives in the bootstrap bucket under
`app/terraform.tfstate`. **CI applies this root** (`tcc-ci-plan` plans on PRs, `tcc-ci-apply`
applies from `master` in the `production` environment); `infra/bootstrap` must be applied first.

| Piece | Choice and why |
|---|---|
| Network | Dedicated VPC `10.20.0.0/16`, one public subnet in `us-east-1a`, IGW. No NAT gateway, no load balancer — one box does not need them and they would add ~US$48/month. |
| Instance | `t3.large` on-demand, Ubuntu 24.04 (Canonical AMI), IMDSv2 required (hop limit 2 so containers can use the instance role), encrypted 30 GB gp3 root. No SSH key, no port 22: access is SSM Session Manager / Run Command. |
| Data volume | gp3 20 GB, encrypted, `prevent_destroy`, mounted at `/srv/tcc/data` by user data (formatted only when blank, fstab by UUID). Replacing the instance reattaches it, so Postgres, Grafana and Caddy data survive. |
| Elastic IP | Stable target for DNS across instance replacements. |
| Security group | 80/tcp, 443/tcp, 443/udp (HTTP/3), 7881/tcp (LiveKit TCP), 50000–60000/udp (LiveKit media), 3478/udp (TURN). TURN/TLS shares 443 via SNI in Caddy. |
| App bucket `tcc-app-<account id>` | Private, SSE-S3, versioned (old versions expire after 30 days), TLS-only. Objects tagged `kind=call-audio` expire after `call_audio_retention_days` (180). |
| Instance role `tcc-app-instance` | App bucket objects, `ssm:GetParametersByPath` on `/tcc/prod`, `kms:Decrypt` via SSM, `AmazonSSMManagedInstanceCore`. Capped by the bootstrap permissions boundary. |
| SSM parameters `/tcc/prod/*` | Plain `String`s owned by Terraform; `SecureString` secrets created with a placeholder and `ignore_changes = [value]`. `deploy.sh` renders all of them into `/opt/tcc/.env`. |
| Budget `tcc-monthly` | Account-wide, US$80/month; email at 80 % and 100 % actual and 100 % forecast. |

The user data only prepares the host (Docker + compose plugin, AWS CLI v2, the data mount,
`/opt/tcc`, `/srv/tcc/data/{postgres,lgtm,caddy}`); the application arrives with the first
deploy. Its log is `/var/log/tcc-user-data.log`.

## Monthly cost estimate (us-east-1, on-demand)

| Item | Calculation | US$/month |
|---|---|---|
| EC2 `t3.large` | 0.0832/h × 730 h | 60.74 |
| Public IPv4 (Elastic IP) | 0.005/h × 730 h | 3.65 |
| EBS gp3 root 30 GB + data 20 GB | 50 GB × 0.08 | 4.00 |
| S3, SSM Standard parameters, Budgets (first 2 free), data transfer | small at demo traffic | < 1 |
| **Total** | | **≈ 68** |

Not included: t3 "unlimited" surplus CPU credits (US$0.05 per vCPU-hour above the 30 %
baseline averaged over 24 h — zero at demo load) and egress beyond the 100 GB/month free tier.
The budget alarm at US$80 is there for either.

## After the first apply

1. **DNS** (Cloudflare, by hand). `terraform output dns_records` prints the same table:

   | Type | Name | Content | Proxy |
   |---|---|---|---|
   | A | `tcc.gabrielfr.com` | `<elastic_ip>` | DNS only |
   | A | `rtc.tcc.gabrielfr.com` | `<elastic_ip>` | DNS only |
   | A | `turn.tcc.gabrielfr.com` | `<elastic_ip>` | DNS only |
   | A | `grafana.tcc.gabrielfr.com` | `<elastic_ip>` | DNS only |

   DNS only (grey cloud) for all four: Cloudflare's proxy does not carry WebRTC/TURN UDP, and
   Caddy has to complete the Let's Encrypt challenge itself.

2. **Secrets**, once per parameter (values never go through Terraform):

   ```sh
   for name in ANTHROPIC_API_KEY LLM_API_KEY ANONYMIZATION_SALT LIVEKIT_API_KEY \
               LIVEKIT_API_SECRET POSTGRES_PASSWORD GRAFANA_ADMIN_PASSWORD GRAFANA_VIEWER_PASSWORD; do
     printf '%s: ' "$name"; read -rs value; echo
     aws ssm put-parameter --name "/tcc/prod/$name" --type SecureString --value "$value" --overwrite
   done
   ```

   Caveat: Terraform still *reads* these on refresh, so the values end up in the state object
   (encrypted, TLS-only bucket, readable by the CI roles only).

3. **Shell on the box**: `aws ssm start-session --target $(terraform output -raw instance_id)`.

## Operations

- **New AMI / different instance type**: `terraform apply -replace=aws_instance.app` (or change
  `instance_type`). The instance is replaced, the data volume and EIP move to the new one.
  The AMI is in `ignore_changes`, so a new Canonical release does not replace the instance on
  its own.
- **Grow the data volume**: raise `data_volume_size_gb`, apply, then on the box
  `sudo resize2fs /dev/disk/by-label/tcc-data`.
- **Destroy**: the data volume has `prevent_destroy`; removing that guard is a conscious edit.
