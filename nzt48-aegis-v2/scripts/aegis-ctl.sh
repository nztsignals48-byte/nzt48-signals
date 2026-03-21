#!/bin/bash
# N10a — AEGIS Remote Control (SSH convenience wrapper)
#
# Usage (from local machine):
#   ssh -i ~/.ssh/nzt48-key.pem ubuntu@3.230.44.22 "bash ~/nzt48-aegis-v2/scripts/aegis-ctl.sh status"
#   ssh -i ~/.ssh/nzt48-key.pem ubuntu@3.230.44.22 "bash ~/nzt48-aegis-v2/scripts/aegis-ctl.sh kill"
#   ssh -i ~/.ssh/nzt48-key.pem ubuntu@3.230.44.22 "bash ~/nzt48-aegis-v2/scripts/aegis-ctl.sh pause"
#   ssh -i ~/.ssh/nzt48-key.pem ubuntu@3.230.44.22 "bash ~/nzt48-aegis-v2/scripts/aegis-ctl.sh resume"
#
# Or from inside the container:
#   docker exec aegis-v2 python3 -m python_brain.ouroboros.kill_switch --status

set -e

CONTAINER="aegis-v2"
CMD="$1"

case "$CMD" in
    kill)
        echo "Sending KILL signal to AEGIS engine..."
        docker exec "$CONTAINER" python3 -m python_brain.ouroboros.kill_switch --kill
        echo "Waiting for engine to stop..."
        sleep 3
        docker exec "$CONTAINER" python3 -m python_brain.ouroboros.kill_switch --status || true
        ;;
    pause)
        echo "Sending PAUSE signal..."
        docker exec "$CONTAINER" python3 -m python_brain.ouroboros.kill_switch --pause
        ;;
    resume)
        echo "Sending RESUME signal..."
        docker exec "$CONTAINER" python3 -m python_brain.ouroboros.kill_switch --resume
        ;;
    status)
        docker exec "$CONTAINER" python3 -m python_brain.ouroboros.kill_switch --status
        ;;
    logs)
        docker logs "$CONTAINER" --tail "${2:-50}"
        ;;
    restart)
        echo "Restarting AEGIS engine container..."
        cd /home/ubuntu/nzt48-aegis-v2
        docker compose restart "$CONTAINER"
        sleep 5
        docker logs "$CONTAINER" --tail 20
        ;;
    *)
        echo "AEGIS V2 Remote Control (N10a)"
        echo ""
        echo "Usage: $0 {kill|pause|resume|status|logs|restart}"
        echo ""
        echo "  kill    — Graceful shutdown (flatten positions, write WAL)"
        echo "  pause   — Freeze signal generation (market data continues)"
        echo "  resume  — Resume signal generation after pause"
        echo "  status  — Show engine status + telemetry"
        echo "  logs    — Show recent container logs (default: 50 lines)"
        echo "  restart — Restart the engine container"
        echo ""
        echo "Remote usage:"
        echo '  ssh -i ~/.ssh/nzt48-key.pem ubuntu@3.230.44.22 "bash ~/nzt48-aegis-v2/scripts/aegis-ctl.sh status"'
        exit 1
        ;;
esac
