data "aws_iam_policy_document" "ecs_exec_assume" {
  statement {
    effect  = "Allow"
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["ecs-tasks.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "ecs_execution" {
  name               = "${var.project_prefix}-ecs-exec"
  assume_role_policy = data.aws_iam_policy_document.ecs_exec_assume.json
}

# Managed policy gives: ecr:GetAuthToken, ecr:BatchCheckLayer…, logs:CreateLogStream, etc.
resource "aws_iam_role_policy_attachment" "ecs_exec_attach" {
  role       = aws_iam_role.ecs_execution.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AmazonECSTaskExecutionRolePolicy"
}
