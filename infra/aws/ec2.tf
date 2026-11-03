# The simulator on EC2 instead of a laptop. Nothing is created until ec2_repo_url is set.
# Design: no SSH, no key pair, no inbound rules (Session Manager for access), IMDSv2 only,
# an instance role instead of access keys, and the instance stops itself when the run ends.

variable "ec2_repo_url" {
  description = "HTTPS URL of your public GitHub repo. Empty = EC2 is not deployed."
  type        = string
  default     = ""
}

variable "ec2_run_args" {
  description = "Arguments passed to `fleetpulse run`. Change them and apply to re-run on a fresh instance."
  type        = string
  default     = "-c configs/scenarios/scenario_22.yaml -t 30 -d 7200 -s firehose --stream-name fleetpulse-telemetry-parquet --region us-west-2"
}

variable "ec2_instance_type" {
  type    = string
  default = "t4g.small"
}

locals {
  ec2_enabled = var.ec2_repo_url != ""
}

data "aws_ami" "al2023_arm" {
  count       = local.ec2_enabled ? 1 : 0
  most_recent = true
  owners      = ["amazon"]

  filter {
    name   = "name"
    values = ["al2023-ami-2023.*-arm64"]
  }
  filter {
    name   = "architecture"
    values = ["arm64"]
  }
}

data "aws_vpc" "default" {
  count   = local.ec2_enabled ? 1 : 0
  default = true
}

data "aws_subnets" "default" {
  count = local.ec2_enabled ? 1 : 0
  filter {
    name   = "vpc-id"
    values = [data.aws_vpc.default[0].id]
  }
}

resource "aws_security_group" "simulator" {
  count       = local.ec2_enabled ? 1 : 0
  name        = "fleetpulse-simulator"
  description = "No inbound access. Outbound HTTPS to Firehose, GitHub and SSM."
  vpc_id      = data.aws_vpc.default[0].id

  egress {
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }
}

data "aws_iam_policy_document" "ec2_assume" {
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["ec2.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "simulator" {
  count              = local.ec2_enabled ? 1 : 0
  name               = "fleetpulse-simulator-role"
  assume_role_policy = data.aws_iam_policy_document.ec2_assume.json
}

# Session Manager access (replaces SSH)
resource "aws_iam_role_policy_attachment" "simulator_ssm" {
  count      = local.ec2_enabled ? 1 : 0
  role       = aws_iam_role.simulator[0].name
  policy_arn = "arn:aws:iam::aws:policy/AmazonSSMManagedInstanceCore"
}

# Least privilege: it may only write to the two telemetry streams
data "aws_iam_policy_document" "simulator" {
  statement {
    actions = ["firehose:PutRecord", "firehose:PutRecordBatch"]
    resources = [
      aws_kinesis_firehose_delivery_stream.telemetry.arn,
      aws_kinesis_firehose_delivery_stream.telemetry_parquet.arn,
    ]
  }
}

resource "aws_iam_role_policy" "simulator" {
  count  = local.ec2_enabled ? 1 : 0
  name   = "fleetpulse-simulator-firehose"
  role   = aws_iam_role.simulator[0].id
  policy = data.aws_iam_policy_document.simulator.json
}

resource "aws_iam_instance_profile" "simulator" {
  count = local.ec2_enabled ? 1 : 0
  name  = "fleetpulse-simulator-profile"
  role  = aws_iam_role.simulator[0].name
}

resource "aws_instance" "simulator" {
  count                  = local.ec2_enabled ? 1 : 0
  ami                    = data.aws_ami.al2023_arm[0].id
  instance_type          = var.ec2_instance_type
  subnet_id              = data.aws_subnets.default[0].ids[0]
  vpc_security_group_ids = [aws_security_group.simulator[0].id]
  iam_instance_profile   = aws_iam_instance_profile.simulator[0].name

  # A public IP is the cheap way to reach Firehose and GitHub from the default VPC.
  # Production would use a private subnet with VPC endpoints.
  associate_public_ip_address = true

  instance_initiated_shutdown_behavior = "stop"
  user_data_replace_on_change          = true
  user_data = templatefile("${path.module}/ec2_user_data.sh.tftpl", {
    repo_url = var.ec2_repo_url
    run_args = var.ec2_run_args
  })

  metadata_options {
    http_tokens = "required"
  }

  root_block_device {
    volume_size = 8
    encrypted   = true
  }

  tags = {
    Name = "fleetpulse-simulator"
  }
}

output "simulator_instance_id" {
  value = local.ec2_enabled ? aws_instance.simulator[0].id : null
}
