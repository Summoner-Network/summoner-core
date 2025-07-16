output "ecr_repository_url" {
  description = "URI to push :latest into (set as $ECR_URI in CI)"
  value       = aws_ecr_repository.splt.repository_url
}

output "ecs_execution_role_arn" {
  value = aws_iam_role.ecs_execution.arn
}

output "ecs_task_role_arn" {
  value = aws_iam_role.ecs_task.arn
}

output "nlb_dns" {
  value = aws_lb.splt.dns_name
}