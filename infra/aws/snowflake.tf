# ---------- Snowflake <-> AWS handshake ----------
# Apply 1: leave both variables empty (role is created with a placeholder trust).
# Apply 2: fill in the two values from `DESC INTEGRATION FLEETPULSE_S3_INT`.
# Apply 3: fill in snowpipe_sqs_arn from `SHOW PIPES` (notification_channel).

variable "snowflake_iam_user_arn" {
  description = "STORAGE_AWS_IAM_USER_ARN from DESC INTEGRATION"
  type        = string
  default     = ""
}

variable "snowflake_external_id" {
  description = "STORAGE_AWS_EXTERNAL_ID from DESC INTEGRATION"
  type        = string
  default     = ""
}

variable "snowpipe_sqs_arn" {
  description = "notification_channel from SHOW PIPES (Snowflake-owned SQS queue)"
  type        = string
  default     = ""
}

locals {
  sf_ready = var.snowflake_iam_user_arn != "" && var.snowflake_external_id != ""
}

data "aws_iam_policy_document" "snowflake_assume" {
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "AWS"
      identifiers = [local.sf_ready ? var.snowflake_iam_user_arn : "arn:aws:iam::${data.aws_caller_identity.current.account_id}:root"]
    }
    dynamic "condition" {
      for_each = local.sf_ready ? [1] : []
      content {
        test     = "StringEquals"
        variable = "sts:ExternalId"
        values   = [var.snowflake_external_id]
      }
    }
  }
}

resource "aws_iam_role" "snowflake" {
  name               = "fleetpulse-snowflake-role"
  assume_role_policy = data.aws_iam_policy_document.snowflake_assume.json
}

data "aws_iam_policy_document" "snowflake_read" {
  statement {
    actions   = ["s3:GetObject", "s3:GetObjectVersion"]
    resources = ["${aws_s3_bucket.raw.arn}/raw/telemetry/*"]
  }
  statement {
    actions   = ["s3:GetBucketLocation"]
    resources = [aws_s3_bucket.raw.arn]
  }
  statement {
    actions   = ["s3:ListBucket"]
    resources = [aws_s3_bucket.raw.arn]
    condition {
      test     = "StringLike"
      variable = "s3:prefix"
      values   = ["raw/telemetry/*", "raw/telemetry"]
    }
  }
}

resource "aws_iam_role_policy" "snowflake" {
  name   = "fleetpulse-snowflake-read"
  role   = aws_iam_role.snowflake.id
  policy = data.aws_iam_policy_document.snowflake_read.json
}

# S3 -> Snowflake's SQS queue, so Snowpipe learns about new files
resource "aws_s3_bucket_notification" "snowpipe" {
  count  = var.snowpipe_sqs_arn == "" ? 0 : 1
  bucket = aws_s3_bucket.raw.id

  queue {
    queue_arn     = var.snowpipe_sqs_arn
    events        = ["s3:ObjectCreated:*"]
    filter_prefix = "raw/telemetry/"
  }
}

output "snowflake_role_arn" {
  value = aws_iam_role.snowflake.arn
}
