output "bucket_name" {
  value = aws_s3_bucket.raw.id
}

output "stream_name" {
  value = aws_kinesis_firehose_delivery_stream.telemetry.name
}

output "producer_policy_arn" {
  description = "Attach to a dedicated IAM user/role used only by the simulator."
  value       = aws_iam_policy.producer.arn
}
