# Second Firehose stream that converts JSON to Parquet in flight.
# The schema comes from a Glue Data Catalog table. The JSON stream stays in place, so the same
# scenario can be sent through either path and compared.

variable "parquet_stream_name" {
  type    = string
  default = "fleetpulse-telemetry-parquet"
}

locals {
  telemetry_columns = {
    run_id        = "string"
    robot_id      = "string"
    seq           = "bigint"
    ts            = "string"
    x             = "double"
    y             = "double"
    heading_deg   = "double"
    speed         = "double"
    battery_pct   = "double"
    motor_current = "double"
    motor_temp    = "double"
    error_code    = "int"
    status        = "string"
  }
}

resource "aws_glue_catalog_database" "telemetry" {
  name = "fleetpulse"
}

resource "aws_glue_catalog_table" "telemetry" {
  name          = "telemetry"
  database_name = aws_glue_catalog_database.telemetry.name
  table_type    = "EXTERNAL_TABLE"

  parameters = {
    classification = "parquet"
  }

  storage_descriptor {
    location      = "s3://${aws_s3_bucket.raw.id}/raw_parquet/telemetry/"
    input_format  = "org.apache.hadoop.hive.ql.io.parquet.MapredParquetInputFormat"
    output_format = "org.apache.hadoop.hive.ql.io.parquet.MapredParquetOutputFormat"

    ser_de_info {
      serialization_library = "org.apache.hadoop.hive.ql.io.parquet.serde.ParquetHiveSerDe"
    }

    dynamic "columns" {
      for_each = local.telemetry_columns
      content {
        name = columns.key
        type = columns.value
      }
    }
  }
}

# Firehose reads the table definition to know the output schema
data "aws_iam_policy_document" "firehose_glue" {
  statement {
    actions = ["glue:GetTable", "glue:GetTableVersion", "glue:GetTableVersions"]
    resources = [
      "arn:aws:glue:${var.region}:${data.aws_caller_identity.current.account_id}:catalog",
      aws_glue_catalog_database.telemetry.arn,
      aws_glue_catalog_table.telemetry.arn,
    ]
  }
}

resource "aws_iam_role_policy" "firehose_glue" {
  name   = "fleetpulse-firehose-glue"
  role   = aws_iam_role.firehose.id
  policy = data.aws_iam_policy_document.firehose_glue.json
}

resource "aws_cloudwatch_log_stream" "firehose_parquet" {
  name           = "S3DeliveryParquet"
  log_group_name = aws_cloudwatch_log_group.firehose.name
}

resource "aws_kinesis_firehose_delivery_stream" "telemetry_parquet" {
  name        = var.parquet_stream_name
  destination = "extended_s3"

  extended_s3_configuration {
    role_arn            = aws_iam_role.firehose.arn
    bucket_arn          = aws_s3_bucket.raw.arn
    prefix              = "raw_parquet/telemetry/year=!{timestamp:yyyy}/month=!{timestamp:MM}/day=!{timestamp:dd}/hour=!{timestamp:HH}/"
    error_output_prefix = "errors_parquet/telemetry/!{firehose:error-output-type}/year=!{timestamp:yyyy}/month=!{timestamp:MM}/day=!{timestamp:dd}/"

    # Format conversion needs a larger buffer (64 MB is the documented minimum at the time of writing;
    # check the Firehose docs). At this volume the 60 s interval flushes first.
    buffering_size     = 64
    buffering_interval = var.buffer_interval_s
    # No compression_format: Parquet is compressed internally (Snappy by default)

    cloudwatch_logging_options {
      enabled         = true
      log_group_name  = aws_cloudwatch_log_group.firehose.name
      log_stream_name = aws_cloudwatch_log_stream.firehose_parquet.name
    }

    data_format_conversion_configuration {
      input_format_configuration {
        deserializer {
          open_x_json_ser_de {}
        }
      }
      output_format_configuration {
        serializer {
          parquet_ser_de {}
        }
      }
      schema_configuration {
        database_name = aws_glue_catalog_database.telemetry.name
        table_name    = aws_glue_catalog_table.telemetry.name
        role_arn      = aws_iam_role.firehose.arn
      }
    }
  }

  depends_on = [aws_iam_role_policy.firehose_glue]
}

output "parquet_stream_name" {
  value = aws_kinesis_firehose_delivery_stream.telemetry_parquet.name
}
