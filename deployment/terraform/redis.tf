# Redis Cluster for Multi-Region State Synchronization
# Phase Q4: Cross-region state replication for zero-loss failover

# ElastiCache subnet groups
resource "aws_elasticache_subnet_group" "primary" {
  provider   = aws.primary
  name       = "nzt48-redis-primary"
  subnet_ids = data.aws_subnets.primary.ids

  tags = {
    Name = "nzt48-redis-primary-subnet"
  }
}

resource "aws_elasticache_subnet_group" "secondary" {
  provider   = aws.secondary
  name       = "nzt48-redis-secondary"
  subnet_ids = data.aws_subnets.secondary.ids

  tags = {
    Name = "nzt48-redis-secondary-subnet"
  }
}

# Security groups for Redis
resource "aws_security_group" "redis_primary" {
  provider    = aws.primary
  name        = "nzt48-redis-primary"
  description = "NZT-48 Redis primary cluster"
  vpc_id      = data.aws_vpc.primary.id

  ingress {
    from_port       = 6379
    to_port         = 6379
    protocol        = "tcp"
    security_groups = [aws_security_group.nzt48_primary.id]
    description     = "Redis from EC2"
  }

  egress {
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }

  tags = {
    Name = "nzt48-redis-primary-sg"
  }
}

resource "aws_security_group" "redis_secondary" {
  provider    = aws.secondary
  name        = "nzt48-redis-secondary"
  description = "NZT-48 Redis secondary cluster"
  vpc_id      = data.aws_vpc.secondary.id

  ingress {
    from_port       = 6379
    to_port         = 6379
    protocol        = "tcp"
    security_groups = [aws_security_group.nzt48_secondary.id]
    description     = "Redis from EC2"
  }

  egress {
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }

  tags = {
    Name = "nzt48-redis-secondary-sg"
  }
}

# Primary Redis cluster (cluster mode enabled for cross-region replication)
resource "aws_elasticache_replication_group" "primary" {
  provider = aws.primary

  replication_group_id       = "nzt48-redis-primary"
  replication_group_description = "NZT-48 primary Redis cluster"

  engine               = "redis"
  engine_version       = "7.0"
  node_type            = "cache.t4g.micro" # Free tier eligible
  num_cache_clusters   = 2                 # Primary + replica for HA
  parameter_group_name = "default.redis7"

  port                       = 6379
  subnet_group_name          = aws_elasticache_subnet_group.primary.name
  security_group_ids         = [aws_security_group.redis_primary.id]

  at_rest_encryption_enabled = true
  transit_encryption_enabled = true
  auth_token                 = var.redis_auth_token

  # Enable automatic failover
  automatic_failover_enabled = true
  multi_az_enabled           = true

  # Backup configuration
  snapshot_retention_limit = 5
  snapshot_window          = "03:00-05:00"
  maintenance_window       = "mon:05:00-mon:07:00"

  # Enable Global Datastore for cross-region replication
  # Note: This requires a separate global_replication_group resource
  # Commented out for initial deployment - can be enabled in phase 2
  # global_replication_group_id = aws_elasticache_global_replication_group.nzt48.id

  tags = {
    Name = "nzt48-redis-primary"
    Role = "primary"
  }
}

# Secondary Redis cluster (replica)
resource "aws_elasticache_replication_group" "secondary" {
  provider = aws.secondary

  replication_group_id          = "nzt48-redis-secondary"
  replication_group_description = "NZT-48 secondary Redis cluster"

  engine               = "redis"
  engine_version       = "7.0"
  node_type            = "cache.t4g.micro"
  num_cache_clusters   = 2
  parameter_group_name = "default.redis7"

  port                       = 6379
  subnet_group_name          = aws_elasticache_subnet_group.secondary.name
  security_group_ids         = [aws_security_group.redis_secondary.id]

  at_rest_encryption_enabled = true
  transit_encryption_enabled = true
  auth_token                 = var.redis_auth_token

  automatic_failover_enabled = true
  multi_az_enabled           = true

  snapshot_retention_limit = 5
  snapshot_window          = "03:00-05:00"
  maintenance_window       = "tue:05:00-tue:07:00"

  tags = {
    Name = "nzt48-redis-secondary"
    Role = "replica"
  }
}

# Global Datastore for cross-region replication (optional, Phase Q4.2)
# Uncomment when ready for full active-active setup
# resource "aws_elasticache_global_replication_group" "nzt48" {
#   provider = aws.primary
#
#   global_replication_group_id_suffix = "nzt48"
#   primary_replication_group_id       = aws_elasticache_replication_group.primary.id
#
#   global_replication_group_description = "NZT-48 cross-region Redis replication"
# }

# CloudWatch alarms for Redis monitoring
resource "aws_cloudwatch_metric_alarm" "redis_cpu_primary" {
  provider = aws.primary

  alarm_name          = "nzt48-redis-primary-cpu"
  comparison_operator = "GreaterThanThreshold"
  evaluation_periods  = "2"
  metric_name         = "CPUUtilization"
  namespace           = "AWS/ElastiCache"
  period              = "120"
  statistic           = "Average"
  threshold           = "75"
  alarm_description   = "Redis primary CPU utilization"
  alarm_actions       = [] # TODO: Add SNS topic for alerts

  dimensions = {
    CacheClusterId = aws_elasticache_replication_group.primary.id
  }
}

resource "aws_cloudwatch_metric_alarm" "redis_memory_primary" {
  provider = aws.primary

  alarm_name          = "nzt48-redis-primary-memory"
  comparison_operator = "GreaterThanThreshold"
  evaluation_periods  = "2"
  metric_name         = "DatabaseMemoryUsagePercentage"
  namespace           = "AWS/ElastiCache"
  period              = "120"
  statistic           = "Average"
  threshold           = "80"
  alarm_description   = "Redis primary memory utilization"

  dimensions = {
    CacheClusterId = aws_elasticache_replication_group.primary.id
  }
}

# Outputs
output "redis_primary_endpoint" {
  value       = aws_elasticache_replication_group.primary.primary_endpoint_address
  description = "Primary Redis cluster endpoint"
  sensitive   = true
}

output "redis_secondary_endpoint" {
  value       = aws_elasticache_replication_group.secondary.primary_endpoint_address
  description = "Secondary Redis cluster endpoint"
  sensitive   = true
}

output "redis_primary_reader_endpoint" {
  value       = aws_elasticache_replication_group.primary.reader_endpoint_address
  description = "Primary Redis reader endpoint"
}

output "redis_secondary_reader_endpoint" {
  value       = aws_elasticache_replication_group.secondary.reader_endpoint_address
  description = "Secondary Redis reader endpoint"
}
