# Only what the public needs to reach. There is no port 22: shells and deploys go through
# SSM (Session Manager / Run Command), which works over the instance's outbound HTTPS.
resource "aws_security_group" "app" {
  name        = "tcc-prod"
  description = "tcc prod: web, LiveKit WebRTC and TURN. No SSH."
  vpc_id      = aws_vpc.main.id

  tags = {
    Name = "tcc-prod"
  }
}

locals {
  # Everything is open to the world on purpose: it is a public web app and a public WebRTC
  # endpoint, so there is no smaller source range to name.
  ingress_rules = {
    http = {
      description = "HTTP: ACME HTTP-01 challenge and redirect to HTTPS (Caddy)"
      protocol    = "tcp"
      from_port   = 80
      to_port     = 80
    }
    https = {
      description = "HTTPS: app, Grafana, LiveKit signaling; TURN/TLS by SNI (Caddy + layer4)"
      protocol    = "tcp"
      from_port   = 443
      to_port     = 443
    }
    https_quic = {
      description = "HTTP/3 (QUIC). Optional: Caddy serves it by default, clients fall back to TCP"
      protocol    = "udp"
      from_port   = 443
      to_port     = 443
    }
    livekit_tcp = {
      description = "LiveKit WebRTC over TCP (ICE/TCP), for networks that block UDP"
      protocol    = "tcp"
      from_port   = 7881
      to_port     = 7881
    }
    livekit_udp = {
      description = "LiveKit WebRTC media (single-port UDP mux)"
      protocol    = "udp"
      from_port   = 7882
      to_port     = 7882
    }
    turn_udp = {
      description = "TURN over UDP (LiveKit embedded TURN)"
      protocol    = "udp"
      from_port   = 3478
      to_port     = 3478
    }
  }
}

resource "aws_vpc_security_group_ingress_rule" "public" {
  for_each = local.ingress_rules

  security_group_id = aws_security_group.app.id
  description       = each.value.description
  ip_protocol       = each.value.protocol
  from_port         = each.value.from_port
  to_port           = each.value.to_port
  cidr_ipv4         = "0.0.0.0/0"

  tags = {
    Name = "tcc-prod-${each.key}"
  }
}

# Unrestricted egress: the box pulls images from GHCR, packages from Ubuntu/Docker mirrors,
# calls the LLM provider, reaches SSM/S3 endpoints and relays WebRTC media to arbitrary
# peers. A narrower list would break the last one by construction (Trivy AVD-AWS-0104).
#trivy:ignore:AVD-AWS-0104
resource "aws_vpc_security_group_egress_rule" "all" {
  security_group_id = aws_security_group.app.id
  description       = "All outbound"
  ip_protocol       = "-1"
  cidr_ipv4         = "0.0.0.0/0"

  tags = {
    Name = "tcc-prod-egress"
  }
}
