locals {
  vpc_cidr       = "10.0.0.0/16"
  public_subnets = cidrsubnets(local.vpc_cidr, 8, 8)  # 10.0.0.0/24, 10.0.1.0/24
}

resource "aws_vpc" "main" {
  cidr_block           = local.vpc_cidr
  enable_dns_hostnames = true
  tags = { Name = "${var.project_prefix}-vpc" }
}

resource "aws_internet_gateway" "igw" {
  vpc_id = aws_vpc.main.id
  tags   = { Name = "${var.project_prefix}-igw" }
}

resource "aws_subnet" "public" {
  for_each = { for i, cidr in local.public_subnets : i => cidr }

  vpc_id                  = aws_vpc.main.id
  cidr_block              = each.value
  map_public_ip_on_launch = true
  availability_zone       = element(data.aws_availability_zones.all.names, each.key)
  tags = { Name = "${var.project_prefix}-public-${each.key}" }
}

data "aws_availability_zones" "all" {}

resource "aws_route_table" "public" {
  vpc_id = aws_vpc.main.id

  route {
    cidr_block = "0.0.0.0/0"
    gateway_id = aws_internet_gateway.igw.id
  }
}

resource "aws_route_table_association" "public" {
  for_each       = aws_subnet.public
  subnet_id      = each.value.id
  route_table_id = aws_route_table.public.id
}

# Security group for tasks ↔ ALB
resource "aws_security_group" "task" {
  name        = "${var.project_prefix}-task-sg"
  description = "Tasks accept NLB traffic on 8888"
  vpc_id      = aws_vpc.main.id

  ingress {
    protocol    = "tcp"
    from_port   = 8888
    to_port     = 8888
    cidr_blocks = ["0.0.0.0/0"]   # or ["10.0.0.0/16"] for VPC-only exposure
  }

  egress {
    protocol    = "-1"
    from_port   = 0
    to_port     = 0
    cidr_blocks = ["0.0.0.0/0"]
  }

  tags = { Project = var.project_prefix }
}
