# NZT-48 Multi-Region Active-Active Infrastructure
# Phase Q4 Deliverable #1: Multi-Region Redundancy
#
# Architecture:
#   - Primary: us-east-1 (Virginia) - existing EC2 instance
#   - Secondary: eu-west-1 (Ireland) - 1-2ms to LSE, failover target
#   - Data sync: RDS PostgreSQL with cross-region replication
#   - State sync: Redis cluster mode (multi-region)
#   - Failover: Route53 geolocation routing + health checks
#
# Expected: <5 min failover, zero trade loss

terraform {
  required_version = ">= 1.5"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.0"
    }
  }

  # State backend: S3 + DynamoDB for locking
  backend "s3" {
    bucket         = "nzt48-terraform-state"
    key            = "production/terraform.tfstate"
    region         = "us-east-1"
    encrypt        = true
    dynamodb_table = "nzt48-terraform-locks"
  }
}

# Primary region provider (us-east-1)
provider "aws" {
  alias  = "primary"
  region = var.primary_region

  default_tags {
    tags = {
      Project     = "NZT-48"
      Environment = var.environment
      ManagedBy   = "Terraform"
      Phase       = "Q4-MultiRegion"
    }
  }
}

# Secondary region provider (eu-west-1)
provider "aws" {
  alias  = "secondary"
  region = var.secondary_region

  default_tags {
    tags = {
      Project     = "NZT-48"
      Environment = var.environment
      ManagedBy   = "Terraform"
      Phase       = "Q4-MultiRegion"
    }
  }
}

# Data sources: existing resources
data "aws_vpc" "primary" {
  provider = aws.primary
  default  = true
}

data "aws_vpc" "secondary" {
  provider = aws.secondary
  default  = true
}

data "aws_subnets" "primary" {
  provider = aws.primary
  filter {
    name   = "vpc-id"
    values = [data.aws_vpc.primary.id]
  }
}

data "aws_subnets" "secondary" {
  provider = aws.secondary
  filter {
    name   = "vpc-id"
    values = [data.aws_vpc.secondary.id]
  }
}

# Security groups for EC2 instances
resource "aws_security_group" "nzt48_primary" {
  provider    = aws.primary
  name        = "nzt48-trading-engine-primary"
  description = "NZT-48 primary trading engine security group"
  vpc_id      = data.aws_vpc.primary.id

  # SSH access
  ingress {
    from_port   = 22
    to_port     = 22
    protocol    = "tcp"
    cidr_blocks = var.allowed_ssh_cidr
    description = "SSH access"
  }

  # API endpoint
  ingress {
    from_port   = 8000
    to_port     = 8000
    protocol    = "tcp"
    cidr_blocks = ["0.0.0.0/0"]
    description = "FastAPI endpoint"
  }

  # IB Gateway TWS
  ingress {
    from_port   = 4002
    to_port     = 4002
    protocol    = "tcp"
    cidr_blocks = ["10.0.0.0/8"]
    description = "IB Gateway internal"
  }

  # Health check endpoint
  ingress {
    from_port   = 8080
    to_port     = 8080
    protocol    = "tcp"
    cidr_blocks = ["0.0.0.0/0"]
    description = "Health check endpoint"
  }

  # Outbound: all traffic
  egress {
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
    description = "All outbound traffic"
  }

  tags = {
    Name = "nzt48-primary-sg"
  }
}

resource "aws_security_group" "nzt48_secondary" {
  provider    = aws.secondary
  name        = "nzt48-trading-engine-secondary"
  description = "NZT-48 secondary trading engine security group"
  vpc_id      = data.aws_vpc.secondary.id

  # Same rules as primary
  ingress {
    from_port   = 22
    to_port     = 22
    protocol    = "tcp"
    cidr_blocks = var.allowed_ssh_cidr
    description = "SSH access"
  }

  ingress {
    from_port   = 8000
    to_port     = 8000
    protocol    = "tcp"
    cidr_blocks = ["0.0.0.0/0"]
    description = "FastAPI endpoint"
  }

  ingress {
    from_port   = 4002
    to_port     = 4002
    protocol    = "tcp"
    cidr_blocks = ["10.0.0.0/8"]
    description = "IB Gateway internal"
  }

  ingress {
    from_port   = 8080
    to_port     = 8080
    protocol    = "tcp"
    cidr_blocks = ["0.0.0.0/0"]
    description = "Health check endpoint"
  }

  egress {
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
    description = "All outbound traffic"
  }

  tags = {
    Name = "nzt48-secondary-sg"
  }
}

# RDS subnet groups
resource "aws_db_subnet_group" "primary" {
  provider   = aws.primary
  name       = "nzt48-rds-primary"
  subnet_ids = data.aws_subnets.primary.ids

  tags = {
    Name = "nzt48-rds-primary"
  }
}

resource "aws_db_subnet_group" "secondary" {
  provider   = aws.secondary
  name       = "nzt48-rds-secondary"
  subnet_ids = data.aws_subnets.secondary.ids

  tags = {
    Name = "nzt48-rds-secondary"
  }
}

# RDS security groups
resource "aws_security_group" "rds_primary" {
  provider    = aws.primary
  name        = "nzt48-rds-primary"
  description = "NZT-48 RDS primary"
  vpc_id      = data.aws_vpc.primary.id

  ingress {
    from_port       = 5432
    to_port         = 5432
    protocol        = "tcp"
    security_groups = [aws_security_group.nzt48_primary.id]
    description     = "PostgreSQL from EC2"
  }

  egress {
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }

  tags = {
    Name = "nzt48-rds-primary-sg"
  }
}

resource "aws_security_group" "rds_secondary" {
  provider    = aws.secondary
  name        = "nzt48-rds-secondary"
  description = "NZT-48 RDS secondary"
  vpc_id      = data.aws_vpc.secondary.id

  ingress {
    from_port       = 5432
    to_port         = 5432
    protocol        = "tcp"
    security_groups = [aws_security_group.nzt48_secondary.id]
    description     = "PostgreSQL from EC2"
  }

  egress {
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }

  tags = {
    Name = "nzt48-rds-secondary-sg"
  }
}

# Primary RDS PostgreSQL instance
resource "aws_db_instance" "primary" {
  provider = aws.primary

  identifier     = "nzt48-primary"
  engine         = "postgres"
  engine_version = "15.4"
  instance_class = var.rds_instance_class

  allocated_storage     = 100
  max_allocated_storage = 500
  storage_type          = "gp3"
  storage_encrypted     = true

  db_name  = "nzt48"
  username = var.db_username
  password = var.db_password

  vpc_security_group_ids = [aws_security_group.rds_primary.id]
  db_subnet_group_name   = aws_db_subnet_group.primary.name

  backup_retention_period = 7
  backup_window           = "03:00-04:00"
  maintenance_window      = "mon:04:00-mon:05:00"

  enabled_cloudwatch_logs_exports = ["postgresql", "upgrade"]
  monitoring_interval             = 60
  monitoring_role_arn             = aws_iam_role.rds_monitoring.arn

  performance_insights_enabled    = true
  performance_insights_kms_key_id = aws_kms_key.rds.arn

  # Enable automated snapshots for disaster recovery
  skip_final_snapshot       = false
  final_snapshot_identifier = "nzt48-primary-final-${formatdate("YYYY-MM-DD-hhmm", timestamp())}"

  # Cross-region replication
  backup_replication_kms_key_id = aws_kms_key.rds_secondary.arn

  tags = {
    Name = "nzt48-primary-rds"
    Role = "primary"
  }
}

# Secondary RDS read replica (cross-region)
resource "aws_db_instance" "secondary" {
  provider = aws.secondary

  identifier          = "nzt48-secondary"
  replicate_source_db = aws_db_instance.primary.arn

  instance_class = var.rds_instance_class

  vpc_security_group_ids = [aws_security_group.rds_secondary.id]
  db_subnet_group_name   = aws_db_subnet_group.secondary.name

  monitoring_interval = 60
  monitoring_role_arn = aws_iam_role.rds_monitoring_secondary.arn

  performance_insights_enabled    = true
  performance_insights_kms_key_id = aws_kms_key.rds_secondary.arn

  # Can be promoted to standalone in failover
  skip_final_snapshot = false

  tags = {
    Name = "nzt48-secondary-rds"
    Role = "replica"
  }
}

# KMS keys for RDS encryption
resource "aws_kms_key" "rds" {
  provider    = aws.primary
  description = "NZT-48 RDS encryption key (primary)"

  tags = {
    Name = "nzt48-rds-primary-key"
  }
}

resource "aws_kms_alias" "rds" {
  provider      = aws.primary
  name          = "alias/nzt48-rds-primary"
  target_key_id = aws_kms_key.rds.key_id
}

resource "aws_kms_key" "rds_secondary" {
  provider    = aws.secondary
  description = "NZT-48 RDS encryption key (secondary)"

  tags = {
    Name = "nzt48-rds-secondary-key"
  }
}

resource "aws_kms_alias" "rds_secondary" {
  provider      = aws.secondary
  name          = "alias/nzt48-rds-secondary"
  target_key_id = aws_kms_key.rds_secondary.key_id
}

# IAM role for RDS enhanced monitoring
resource "aws_iam_role" "rds_monitoring" {
  provider = aws.primary
  name     = "nzt48-rds-monitoring-primary"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Action = "sts:AssumeRole"
      Effect = "Allow"
      Principal = {
        Service = "monitoring.rds.amazonaws.com"
      }
    }]
  })
}

resource "aws_iam_role_policy_attachment" "rds_monitoring" {
  provider   = aws.primary
  role       = aws_iam_role.rds_monitoring.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AmazonRDSEnhancedMonitoringRole"
}

resource "aws_iam_role" "rds_monitoring_secondary" {
  provider = aws.secondary
  name     = "nzt48-rds-monitoring-secondary"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Action = "sts:AssumeRole"
      Effect = "Allow"
      Principal = {
        Service = "monitoring.rds.amazonaws.com"
      }
    }]
  })
}

resource "aws_iam_role_policy_attachment" "rds_monitoring_secondary" {
  provider   = aws.secondary
  role       = aws_iam_role.rds_monitoring_secondary.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AmazonRDSEnhancedMonitoringRole"
}

# Route53 health checks
resource "aws_route53_health_check" "primary" {
  provider          = aws.primary
  fqdn              = aws_eip.primary.public_dns
  port              = 8080
  type              = "HTTP"
  resource_path     = "/health"
  failure_threshold = "3"
  request_interval  = "30"

  tags = {
    Name = "nzt48-primary-health"
  }
}

resource "aws_route53_health_check" "secondary" {
  provider          = aws.secondary
  fqdn              = aws_eip.secondary.public_dns
  port              = 8080
  type              = "HTTP"
  resource_path     = "/health"
  failure_threshold = "3"
  request_interval  = "30"

  tags = {
    Name = "nzt48-secondary-health"
  }
}

# Elastic IPs for stable addressing
resource "aws_eip" "primary" {
  provider = aws.primary
  domain   = "vpc"

  tags = {
    Name = "nzt48-primary-eip"
  }
}

resource "aws_eip" "secondary" {
  provider = aws.secondary
  domain   = "vpc"

  tags = {
    Name = "nzt48-secondary-eip"
  }
}

# NOTE: EC2 instance creation is commented out - use existing instance
# resource "aws_instance" "primary" {
#   provider      = aws.primary
#   ami           = var.ami_id_primary
#   instance_type = "c7i-flex.large"
#   ...
# }

# Outputs
output "primary_rds_endpoint" {
  value       = aws_db_instance.primary.endpoint
  description = "Primary RDS endpoint"
  sensitive   = true
}

output "secondary_rds_endpoint" {
  value       = aws_db_instance.secondary.endpoint
  description = "Secondary RDS read replica endpoint"
  sensitive   = true
}

output "primary_eip" {
  value       = aws_eip.primary.public_ip
  description = "Primary region Elastic IP"
}

output "secondary_eip" {
  value       = aws_eip.secondary.public_ip
  description = "Secondary region Elastic IP"
}

output "primary_health_check_id" {
  value       = aws_route53_health_check.primary.id
  description = "Primary health check ID"
}

output "secondary_health_check_id" {
  value       = aws_route53_health_check.secondary.id
  description = "Secondary health check ID"
}
