# AEGIS V2 — Live trading instance configuration.
# Non-burstable CPU for deterministic hot-path execution.
# Apply: terraform plan -var-file=variables.live.tfvars
# DO NOT apply during paper trading — paper is fine on c7i-flex.large.

instance_type    = "c7i.large"
root_volume_size = 100
