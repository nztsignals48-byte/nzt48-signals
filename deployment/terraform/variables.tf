# NZT-48 Multi-Region Infrastructure Variables

variable "environment" {
  description = "Environment name"
  type        = string
  default     = "production"
}

variable "primary_region" {
  description = "Primary AWS region"
  type        = string
  default     = "us-east-1"
}

variable "secondary_region" {
  description = "Secondary AWS region for failover"
  type        = string
  default     = "eu-west-1"
}

variable "allowed_ssh_cidr" {
  description = "CIDR blocks allowed for SSH access"
  type        = list(string)
  default     = ["0.0.0.0/0"] # TODO: Restrict to specific IPs
}

variable "rds_instance_class" {
  description = "RDS instance class"
  type        = string
  default     = "db.t4g.micro" # Free tier eligible
}

variable "db_username" {
  description = "RDS master username"
  type        = string
  default     = "nzt48admin"
  sensitive   = true
}

variable "db_password" {
  description = "RDS master password"
  type        = string
  sensitive   = true
}

variable "ami_id_primary" {
  description = "AMI ID for primary EC2 instance"
  type        = string
  default     = "ami-0c55b159cbfafe1f0" # Amazon Linux 2023 us-east-1
}

variable "ami_id_secondary" {
  description = "AMI ID for secondary EC2 instance"
  type        = string
  default     = "ami-0d71ea30463e0ff8d" # Amazon Linux 2023 eu-west-1
}

variable "enable_redis_cluster" {
  description = "Enable Redis cluster mode for multi-region state sync"
  type        = bool
  default     = false # Start with standalone Redis, upgrade later
}

variable "enable_route53_failover" {
  description = "Enable Route53 automatic failover"
  type        = bool
  default     = true
}

variable "health_check_interval_seconds" {
  description = "Route53 health check interval"
  type        = number
  default     = 30
}

variable "health_check_failure_threshold" {
  description = "Health check failure threshold"
  type        = number
  default     = 3
}
