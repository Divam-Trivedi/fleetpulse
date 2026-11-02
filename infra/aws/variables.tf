variable "region" {
  description = "Use the same region as your Snowflake account to avoid cross-region transfer costs."
  type        = string
  default     = "us-west-2"
}

variable "aws_profile" {
  type    = string
  default = "fleetpulse"
}

variable "stream_name" {
  type    = string
  default = "fleetpulse-telemetry"
}

variable "raw_retention_days" {
  description = "Expire raw files after N days to cap storage cost."
  type        = number
  default     = 14
}

variable "buffer_interval_s" {
  description = "Firehose flush interval. Lower = fresher data, more small files."
  type        = number
  default     = 60
}

variable "buffer_size_mb" {
  type    = number
  default = 5
}
