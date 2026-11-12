# A dedicated VPC with one public subnet. One box needs no private tier: there is no NAT
# gateway (US$32/month before traffic) and no load balancer (~US$16/month); the instance
# talks to the internet through the IGW, and Caddy on the box terminates TLS.
#
# No VPC Flow Logs (Trivy AVD-AWS-0178): with one instance and every port it opens being
# public by design, the logs would record internet noise at CloudWatch ingestion prices;
# the request-level view lives in Caddy's logs in Loki.
#trivy:ignore:AVD-AWS-0178
resource "aws_vpc" "main" {
  cidr_block           = "10.20.0.0/16"
  enable_dns_support   = true
  enable_dns_hostnames = true

  tags = {
    Name = "tcc-prod"
  }
}

resource "aws_internet_gateway" "main" {
  vpc_id = aws_vpc.main.id

  tags = {
    Name = "tcc-prod"
  }
}

# Instances here get no automatic public IP from the subnet (map_public_ip_on_launch off);
# the instance asks for one explicitly and then wears the Elastic IP.
resource "aws_subnet" "public" {
  vpc_id            = aws_vpc.main.id
  cidr_block        = "10.20.1.0/24"
  availability_zone = var.availability_zone

  tags = {
    Name = "tcc-prod-public"
  }
}

resource "aws_route_table" "public" {
  vpc_id = aws_vpc.main.id

  route {
    cidr_block = "0.0.0.0/0"
    gateway_id = aws_internet_gateway.main.id
  }

  tags = {
    Name = "tcc-prod-public"
  }
}

resource "aws_route_table_association" "public" {
  subnet_id      = aws_subnet.public.id
  route_table_id = aws_route_table.public.id
}
