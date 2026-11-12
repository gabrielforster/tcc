output "elastic_ip" {
  description = "Public IPv4 every DNS record points at."
  value       = aws_eip.app.public_ip
}

output "instance_id" {
  description = "EC2 instance id (Session Manager: aws ssm start-session --target <id>)."
  value       = aws_instance.app.id
}

output "app_bucket" {
  description = "App object storage bucket (also SSM parameter /tcc/prod/S3_BUCKET)."
  value       = aws_s3_bucket.app.bucket
}

output "data_volume_id" {
  description = "EBS volume holding Postgres/Grafana/Caddy data."
  value       = aws_ebs_volume.data.id
}

# The records Gabriel creates by hand in Cloudflare. DNS only (grey cloud): the Cloudflare
# proxy carries HTTP but not WebRTC/TURN UDP, and Caddy needs to answer the ACME challenge
# itself.
output "dns_records" {
  description = "Cloudflare records to create (type A, DNS only)."
  value = {
    for name in [var.app_domain, var.rtc_domain, var.turn_domain, var.grafana_domain] :
    name => { type = "A", value = aws_eip.app.public_ip, proxied = false }
  }
}
