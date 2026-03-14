# Bare Metal / Dedicated Host Migration Plan (Phase K-20)

## Current State
- EC2 c7i-flex.large (shared tenancy, 4GB RAM, 2 vCPUs)
- CPU steal-time introduces 1-5ms jitter
- Docker Compose: nzt48 + ib-gateway + redis

## Target Options
| Instance | vCPUs | RAM | Region | Est. Cost/mo |
|----------|-------|-----|--------|-------------|
| c7i-flex.large dedicated | 2 | 4GB | us-east-1 | ~$80 |
| c7g.medium | 1 | 2GB | eu-west-2 | ~$30 |
| c7g.large | 2 | 4GB | eu-west-2 | ~$60 |

## Benefits
- Eliminates CPU steal-time (hypervisor jitter)
- Consistent latency for time-critical operations
- eu-west-2 (London) = closer to LSE for lower network latency

## Migration Steps
1. Launch new instance in target region
2. Install Docker, docker-compose, AWS CLI
3. Copy configuration and data from current instance
4. Run parallel operation for 1 week
5. Validate: compare scan loop timing, order latency
6. Cut over DNS/Elastic IP
7. Decommission old instance

## Validation Criteria
- CPU steal-time < 0.1% (vs current ~2-5%)
- Scan loop p99 latency < 500ms
- Order submission p99 < 100ms
- No missed scan cycles in 7 days
