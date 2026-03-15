# Route53 DNS-based Failover Configuration
# Phase Q4: Automatic failover with geolocation routing

# Route53 hosted zone (assumes zone already exists)
data "aws_route53_zone" "main" {
  name         = var.route53_zone_name
  private_zone = false
}

# Primary region A record with health check
resource "aws_route53_record" "primary" {
  zone_id = data.aws_route53_zone.main.zone_id
  name    = "nzt48.${var.route53_zone_name}"
  type    = "A"
  ttl     = 60

  set_identifier  = "primary"
  health_check_id = aws_route53_health_check.primary.id

  failover_routing_policy {
    type = "PRIMARY"
  }

  records = [aws_eip.primary.public_ip]
}

# Secondary region A record with health check
resource "aws_route53_record" "secondary" {
  zone_id = data.aws_route53_zone.main.zone_id
  name    = "nzt48.${var.route53_zone_name}"
  type    = "A"
  ttl     = 60

  set_identifier  = "secondary"
  health_check_id = aws_route53_health_check.secondary.id

  failover_routing_policy {
    type = "SECONDARY"
  }

  records = [aws_eip.secondary.public_ip]
}

# API endpoint with geolocation routing
resource "aws_route53_record" "api_us" {
  zone_id = data.aws_route53_zone.main.zone_id
  name    = "api.${var.route53_zone_name}"
  type    = "A"
  ttl     = 60

  set_identifier = "api-us"

  geolocation_routing_policy {
    continent = "NA" # North America
  }

  records = [aws_eip.primary.public_ip]
}

resource "aws_route53_record" "api_eu" {
  zone_id = data.aws_route53_zone.main.zone_id
  name    = "api.${var.route53_zone_name}"
  type    = "A"
  ttl     = 60

  set_identifier = "api-eu"

  geolocation_routing_policy {
    continent = "EU" # Europe
  }

  records = [aws_eip.secondary.public_ip]
}

resource "aws_route53_record" "api_default" {
  zone_id = data.aws_route53_zone.main.zone_id
  name    = "api.${var.route53_zone_name}"
  type    = "A"
  ttl     = 60

  set_identifier = "api-default"

  geolocation_routing_policy {
    country = "*" # Default for all other locations
  }

  records = [aws_eip.primary.public_ip]
}

# CloudWatch alarm for health check failures
resource "aws_cloudwatch_metric_alarm" "primary_unhealthy" {
  provider = aws.primary

  alarm_name          = "nzt48-primary-unhealthy"
  comparison_operator = "LessThanThreshold"
  evaluation_periods  = "2"
  metric_name         = "HealthCheckStatus"
  namespace           = "AWS/Route53"
  period              = "60"
  statistic           = "Minimum"
  threshold           = "1"
  alarm_description   = "Primary endpoint health check failing"
  treat_missing_data  = "breaching"

  dimensions = {
    HealthCheckId = aws_route53_health_check.primary.id
  }

  alarm_actions = [] # TODO: Add SNS topic for PagerDuty/SMS alerts
}

resource "aws_cloudwatch_metric_alarm" "secondary_unhealthy" {
  provider = aws.secondary

  alarm_name          = "nzt48-secondary-unhealthy"
  comparison_operator = "LessThanThreshold"
  evaluation_periods  = "2"
  metric_name         = "HealthCheckStatus"
  namespace           = "AWS/Route53"
  period              = "60"
  statistic           = "Minimum"
  threshold           = "1"
  alarm_description   = "Secondary endpoint health check failing"
  treat_missing_data  = "breaching"

  dimensions = {
    HealthCheckId = aws_route53_health_check.secondary.id
  }

  alarm_actions = []
}

# Outputs
output "primary_endpoint_dns" {
  value       = aws_route53_record.primary.fqdn
  description = "Primary endpoint DNS name"
}

output "secondary_endpoint_dns" {
  value       = aws_route53_record.secondary.fqdn
  description = "Secondary endpoint DNS name"
}

output "api_endpoint_dns" {
  value       = "api.${var.route53_zone_name}"
  description = "API endpoint with geolocation routing"
}
