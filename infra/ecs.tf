# 1️⃣ Cluster
resource "aws_ecs_cluster" "main" {
  name = "${var.project_prefix}-cluster"
  tags = { Project = var.project_prefix }
}

# 2️⃣ Task definition
data "aws_ecr_repository" "splt" {
  name = "${lower(var.project_prefix)}-splt"
}

resource "aws_ecs_task_definition" "splt" {
  family                   = "${var.project_prefix}-task"
  network_mode             = "awsvpc"
  requires_compatibilities = ["FARGATE"]
  cpu                      = 512   # 0.5 vCPU
  memory                   = 1024  # 1 GiB

  execution_role_arn = aws_iam_role.ecs_execution.arn
  task_role_arn      = aws_iam_role.ecs_task.arn

  container_definitions = jsonencode([
    {
      name      = "splt"
      image     = "${data.aws_ecr_repository.splt.repository_url}:latest"
      portMappings = [
        { containerPort = 8888, hostPort = 8888, protocol = "tcp" }
      ]
      environment = [
        { name = "SUMMONER_CONFIG", value = "/app/templates/server_config.json" }
      ]
      essential = true
      logConfiguration = {
        logDriver = "awslogs"
        options = {
          awslogs-region        = var.aws_region
          awslogs-group         = "/ecs/${var.project_prefix}"
          awslogs-stream-prefix = "ecs"
        }
      }
    }
  ])
}

# 3️⃣ Service
resource "aws_ecs_service" "splt" {
  name            = "${var.project_prefix}-svc"
  cluster         = aws_ecs_cluster.main.id
  task_definition = aws_ecs_task_definition.splt.arn
  desired_count   = 1
  launch_type     = "FARGATE"
  force_new_deployment = true

  network_configuration {
    subnets         = [for s in aws_subnet.public : s.id]
    security_groups = [aws_security_group.task.id]
    assign_public_ip = true
  }

  load_balancer {
    target_group_arn = aws_lb_target_group.splt.arn
    container_name   = "splt"
    container_port   = 8888
  }

  depends_on = [aws_lb_listener.tcp]
}
