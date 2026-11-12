# Latest Ubuntu 24.04 LTS from Canonical's own account. Ubuntu over Amazon Linux: same
# price, the SSM agent ships with it, and it is the distro the app is developed on.
data "aws_ami" "ubuntu" {
  most_recent = true
  owners      = ["099720109477"] # Canonical

  filter {
    name   = "name"
    values = ["ubuntu/images/hvm-ssd-gp3/ubuntu-noble-24.04-amd64-server-*"]
  }

  filter {
    name   = "virtualization-type"
    values = ["hvm"]
  }
}

resource "aws_instance" "app" {
  ami                         = data.aws_ami.ubuntu.id
  instance_type               = var.instance_type
  subnet_id                   = aws_subnet.public.id
  vpc_security_group_ids      = [aws_security_group.app.id]
  iam_instance_profile        = aws_iam_instance_profile.instance.name
  availability_zone           = var.availability_zone
  monitoring                  = false # detailed CloudWatch metrics: the Grafana stack covers this
  ebs_optimized               = true
  associate_public_ip_address = true # outbound access during first boot, before the EIP lands

  # On-demand, unlimited: during a demo a throttled CPU is worse than a few cents of surplus
  # credits; the budget alarm catches a runaway.
  credit_specification {
    cpu_credits = "unlimited"
  }

  # IMDSv2 only (no unauthenticated metadata reads, the classic SSRF-to-credentials path).
  # Hop limit 2, not 1: the app reaches the instance role from inside a container on the
  # Docker bridge, one network hop further than the host, and with 1 its token request
  # would be dropped and boto3 would find no credentials.
  metadata_options {
    http_endpoint               = "enabled"
    http_tokens                 = "required"
    http_put_response_hop_limit = 2
    instance_metadata_tags      = "disabled"
  }

  root_block_device {
    volume_type           = "gp3"
    volume_size           = var.root_volume_size_gb
    encrypted             = true
    delete_on_termination = true
  }

  user_data = templatefile("${path.module}/user_data.sh.tftpl", {
    data_volume_id = aws_ebs_volume.data.id
  })
  # User data only runs on first boot, so a change to it means nothing until a new
  # instance boots with it. Replacing the instance is safe: all state is on the data volume.
  user_data_replace_on_change = true

  tags = {
    Name = "tcc-prod"
  }

  lifecycle {
    # most_recent resolves to a new AMI whenever Canonical publishes one; without this every
    # apply after a release would replace the instance. Rebuilding on a fresh image is a
    # deliberate act: `terraform apply -replace=aws_instance.app`.
    ignore_changes = [ami]
  }
}

# Postgres, Grafana stack and Caddy data. A separate volume, not the root disk, so the
# instance is disposable: replace it (new AMI, other type, spot) and the data reattaches.
# user_data.sh.tftpl mounts it at /srv/tcc/data and formats it only if it is blank.
#
# Encrypted with the AWS-managed aws/ebs key, not a customer-managed one (Trivy
# AVD-AWS-0027): the volume is only ever attached to this instance, and a CMK would add
# cost and a key policy to maintain without changing who can read the data.
#trivy:ignore:AVD-AWS-0027
resource "aws_ebs_volume" "data" {
  availability_zone = var.availability_zone
  size              = var.data_volume_size_gb
  type              = "gp3"
  encrypted         = true

  tags = {
    Name = "tcc-prod-data"
  }

  # The database lives here. A plan that would delete it fails instead.
  lifecycle {
    prevent_destroy = true
  }
}

resource "aws_volume_attachment" "data" {
  device_name = "/dev/sdf" # surfaces as an NVMe device on Nitro; user data finds it by volume id
  volume_id   = aws_ebs_volume.data.id
  instance_id = aws_instance.app.id

  # Detach from a stopped instance so Postgres is not cut off mid-write when the instance
  # is replaced.
  stop_instance_before_detaching = true
}

# A stable public address for the DNS A records. Re-associated on instance replacement, so
# DNS never changes. (Every public IPv4 costs US$0.005/h since 2024, attached or not.)
resource "aws_eip" "app" {
  domain = "vpc"

  tags = {
    Name = "tcc-prod"
  }
}

resource "aws_eip_association" "app" {
  allocation_id = aws_eip.app.id
  instance_id   = aws_instance.app.id
}
