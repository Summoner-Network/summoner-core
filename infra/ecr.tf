resource "aws_ecr_repository" "splt" {
  name = "${lower(var.project_prefix)}-splt"
  image_tag_mutability = "MUTABLE"         # GH Actions will tag :latest on every push
  image_scanning_configuration {
    scan_on_push = true
  }
  lifecycle {
    # If you   terraform destroy   we *keep* the repo so you don’t lose releases.
    prevent_destroy = true
  }
  tags = {
    Project = var.project_prefix
  }
}
