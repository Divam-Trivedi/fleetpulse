# EventBridge schedule -> Lambda -> Snowflake -> SNS email.
# Nothing here is created until alert_email is set in terraform.tfvars.
# Build the package first: ./scripts/build_lambda.sh

variable "alert_email" {
  description = "Email address for robot alerts. Empty = alerting is not deployed."
  type        = string
  default     = ""
}

variable "alert_schedule" {
  type    = string
  default = "rate(5 minutes)"
}

variable "alert_schedule_state" {
  description = "DISABLED by default: every run wakes the Snowflake warehouse. Set ENABLED while demoing."
  type        = string
  default     = "DISABLED"
  validation {
    condition     = contains(["ENABLED", "DISABLED"], var.alert_schedule_state)
    error_message = "Must be ENABLED or DISABLED."
  }
}

locals {
  alerting   = var.alert_email != ""
  lambda_zip = "${path.module}/../../build/alert_lambda.zip"
}

resource "aws_sns_topic" "alerts" {
  count = local.alerting ? 1 : 0
  name  = "fleetpulse-alerts"
}

# The recipient must click the confirmation link AWS emails after apply.
resource "aws_sns_topic_subscription" "email" {
  count     = local.alerting ? 1 : 0
  topic_arn = aws_sns_topic.alerts[0].arn
  protocol  = "email"
  endpoint  = var.alert_email
}

# Container only. The private key is stored out-of-band (scripts/put_alert_secret.py), never in Terraform state.
resource "aws_secretsmanager_secret" "snowflake_alert" {
  count                   = local.alerting ? 1 : 0
  name                    = "fleetpulse/snowflake-alert"
  recovery_window_in_days = 0
}

resource "aws_cloudwatch_log_group" "alert_lambda" {
  count             = local.alerting ? 1 : 0
  name              = "/aws/lambda/fleetpulse-alerts"
  retention_in_days = 7
}

data "aws_iam_policy_document" "lambda_assume" {
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["lambda.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "alert_lambda" {
  count              = local.alerting ? 1 : 0
  name               = "fleetpulse-alert-lambda-role"
  assume_role_policy = data.aws_iam_policy_document.lambda_assume.json
}

data "aws_iam_policy_document" "alert_lambda" {
  count = local.alerting ? 1 : 0

  statement {
    actions   = ["secretsmanager:GetSecretValue"]
    resources = [aws_secretsmanager_secret.snowflake_alert[0].arn]
  }
  statement {
    actions   = ["sns:Publish"]
    resources = [aws_sns_topic.alerts[0].arn]
  }
  statement {
    actions   = ["logs:CreateLogStream", "logs:PutLogEvents"]
    resources = ["${aws_cloudwatch_log_group.alert_lambda[0].arn}:*"]
  }
}

resource "aws_iam_role_policy" "alert_lambda" {
  count  = local.alerting ? 1 : 0
  name   = "fleetpulse-alert-lambda-access"
  role   = aws_iam_role.alert_lambda[0].id
  policy = data.aws_iam_policy_document.alert_lambda[0].json
}

resource "aws_lambda_function" "alerts" {
  count            = local.alerting ? 1 : 0
  function_name    = "fleetpulse-alerts"
  role             = aws_iam_role.alert_lambda[0].arn
  handler          = "alert_handler.lambda_handler"
  runtime          = "python3.12"
  filename         = local.lambda_zip
  source_code_hash = try(filebase64sha256(local.lambda_zip), null)
  timeout          = 60
  memory_size      = 256

  environment {
    variables = {
      SECRET_ARN = aws_secretsmanager_secret.snowflake_alert[0].arn
      TOPIC_ARN  = aws_sns_topic.alerts[0].arn
    }
  }

  depends_on = [aws_cloudwatch_log_group.alert_lambda]
}

resource "aws_cloudwatch_event_rule" "alert_schedule" {
  count               = local.alerting ? 1 : 0
  name                = "fleetpulse-alert-schedule"
  schedule_expression = var.alert_schedule
  state               = var.alert_schedule_state
}

resource "aws_cloudwatch_event_target" "alert_lambda" {
  count = local.alerting ? 1 : 0
  rule  = aws_cloudwatch_event_rule.alert_schedule[0].name
  arn   = aws_lambda_function.alerts[0].arn
}

resource "aws_lambda_permission" "events" {
  count         = local.alerting ? 1 : 0
  statement_id  = "AllowEventBridgeInvoke"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.alerts[0].function_name
  principal     = "events.amazonaws.com"
  source_arn    = aws_cloudwatch_event_rule.alert_schedule[0].arn
}

output "alert_topic_arn" {
  value = local.alerting ? aws_sns_topic.alerts[0].arn : null
}

output "alert_lambda_name" {
  value = local.alerting ? aws_lambda_function.alerts[0].function_name : null
}
