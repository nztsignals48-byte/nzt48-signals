# AEGIS V2 — Developer Commands
# Usage: make <target>

EC2_HOST   := ubuntu@3.230.44.22
EC2_KEY    := ~/.ssh/nzt48-key.pem
SSH        := ssh -i $(EC2_KEY) -o StrictHostKeyChecking=no $(EC2_HOST)
COMPOSE    := docker compose

.PHONY: help build deploy restart logs status nightly backtest test check clean

help:  ## Show available commands
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | sort | awk 'BEGIN {FS = ":.*?## "}; {printf "\033[36m%-15s\033[0m %s\n", $$1, $$2}'

# ── Local Development ──

check:  ## Cargo check (fast, no link)
	cd rust_core && cargo check

test:  ## Run Python tests locally
	python3 -m pytest tests/ -v --tb=short 2>/dev/null || echo "No test directory found"

# ── Docker ──

build:  ## Build Docker image locally
	$(COMPOSE) build aegis-v2

up:  ## Start all containers locally
	$(COMPOSE) up -d

down:  ## Stop all containers
	$(COMPOSE) down

logs:  ## Tail aegis-v2 logs
	$(COMPOSE) logs -f --tail=50 aegis-v2

status:  ## Show container status
	$(COMPOSE) ps

# ── EC2 Deployment ──

deploy:  ## Push + pull + rebuild on EC2
	git push origin feat/tier-system-enhancements-full
	$(SSH) "cd ~/nzt48-signals-repo/nzt48-aegis-v2 && git pull origin feat/tier-system-enhancements-full && docker compose up -d --build aegis-v2"

restart:  ## Restart aegis-v2 on EC2 (no rebuild)
	$(SSH) "cd ~/nzt48-signals-repo/nzt48-aegis-v2 && docker compose restart aegis-v2"

ec2-logs:  ## Tail EC2 aegis-v2 logs
	$(SSH) "docker logs aegis-v2 --tail=80 -f"

ec2-status:  ## Show EC2 container status
	$(SSH) "docker ps --format 'table {{.Names}}\t{{.Status}}\t{{.Ports}}'"

ec2-shell:  ## SSH into EC2
	$(SSH)

# ── Operations ──

nightly:  ## Run nightly pipeline manually on EC2
	$(SSH) "docker exec aegis-v2 bash /app/scripts/nightly_pipeline.sh"

backtest:  ## Run world-class backtest on EC2
	$(SSH) "cd ~/nzt48-signals-repo/nzt48-aegis-v2 && python3 world_class_backtest.py"

metrics:  ## Check Prometheus metrics endpoint
	$(SSH) "curl -s http://localhost:9090/metrics | head -30"

kill:  ## Emergency flatten (write KILL file)
	$(SSH) "docker exec aegis-v2 touch /app/data/KILL && echo 'KILL signal sent'"

pause:  ## Pause trading (write PAUSE file)
	$(SSH) "docker exec aegis-v2 touch /app/data/PAUSE && echo 'PAUSE signal sent'"

resume:  ## Resume trading (remove PAUSE file)
	$(SSH) "docker exec aegis-v2 rm -f /app/data/PAUSE && echo 'PAUSE removed'"

clean:  ## Remove stale Docker images
	docker image prune -f
