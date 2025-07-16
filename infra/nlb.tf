###############################################################################
# infra/nlb.tf – Network Load Balancer on :8888 (no health check)            #
###############################################################################

resource "aws_lb" "splt" {
  name               = "${var.project_prefix}-nlb"
  internal           = false
  load_balancer_type = "network"          # ⬅️ was "application"
  subnets            = [for s in aws_subnet.public : s.id]

  # NLBs can’t use security groups; they create one ENI per subnet automatically
  enable_deletion_protection = false

  tags = { Project = var.project_prefix }
}

# Target group – TCP :8888, health checks disabled
resource "aws_lb_target_group" "splt" {
  name        = "${var.project_prefix}-tg"
  port        = 8888
  protocol    = "TCP"
  vpc_id      = aws_vpc.main.id
  target_type = "ip"

  # 👉 Omit the whole `health_check` block entirely
  #    (or keep an empty block; enabled=true, TCP is implicit)
  # health_check {
  #   protocol = "TCP"
  # }

  tags = { Project = var.project_prefix }
}

# Listener – TCP passthrough
resource "aws_lb_listener" "tcp" {
  load_balancer_arn = aws_lb.splt.arn
  port              = 8888
  protocol          = "TCP"

  default_action {
    type             = "forward"
    target_group_arn = aws_lb_target_group.splt.arn
  }
}
