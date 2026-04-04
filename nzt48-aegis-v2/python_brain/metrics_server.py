"""Enhanced Prometheus metrics endpoint for AEGIS V2.
Runs on port 9090 inside the container. Scraped by Prometheus every 15s.
Reads WAL size, equity, trade count, regime state, macro data, enrichment status.
"""
import http.server
import json
import os
import time
import threading

PORT = 9090
METRICS_INTERVAL = 10  # seconds between metric refreshes

_cached_metrics = ""
_last_refresh = 0


def _collect_metrics():
    """Collect metrics from files and return Prometheus text format."""
    lines = []

    # WAL size
    wal = "/app/events/current.ndjson"
    if os.path.exists(wal):
        wal_bytes = os.path.getsize(wal)
        wal_lines = sum(1 for _ in open(wal)) if wal_bytes < 50_000_000 else 0
        lines.append(f"aegis_wal_bytes {wal_bytes}")
        lines.append(f"aegis_wal_events {wal_lines}")

    # Dynamic weights
    dw = "/app/config/dynamic_weights.toml"
    if os.path.exists(dw):
        with open(dw) as f:
            for line in f:
                if line.startswith("win_rate"):
                    val = line.split("=")[1].strip().split("#")[0].strip()
                    lines.append(f"aegis_win_rate {val}")
                elif line.startswith("trade_count"):
                    val = line.split("=")[1].strip().split("#")[0].strip()
                    lines.append(f"aegis_trade_count {val}")

    # System memory (if exists)
    mem = "/app/data/system_memory.json"
    if os.path.exists(mem):
        try:
            with open(mem) as f:
                m = json.load(f)
            tc = m.get("bayesian", {}).get("trade_count", 0)
            lines.append(f"aegis_memory_trade_count {tc}")
        except Exception:
            pass

    # Disk usage
    try:
        st = os.statvfs("/")
        pct = (1 - st.f_bavail / st.f_blocks) * 100
        lines.append(f"aegis_disk_usage_percent {pct:.1f}")
    except Exception:
        pass

    # Uptime
    try:
        with open("/proc/uptime") as f:
            uptime = float(f.read().split()[0])
            lines.append(f"aegis_uptime_seconds {uptime:.0f}")
    except Exception:
        pass

    # ── NEW: Regime state metrics ──
    regime_file = "/app/config/regime_state.json"
    if os.path.exists(regime_file):
        try:
            with open(regime_file) as f:
                regime = json.load(f)
            regime_name = regime.get("current_regime", "UNKNOWN")
            regime_prob = regime.get("regime_probability", 0)
            # Encode regime as numeric: LOW_VOL=0, NORMAL=1, HIGH_VOL=2
            regime_code = {"LOW_VOL": 0, "NORMAL": 1, "HIGH_VOL": 2}.get(regime_name, -1)
            lines.append(f"aegis_regime_state {regime_code}")
            lines.append(f"aegis_regime_probability {regime_prob:.3f}")
            for label, prob in regime.get("smoothed_probs", {}).items():
                safe_label = label.lower().replace(" ", "_")
                lines.append(f'aegis_regime_prob{{regime="{safe_label}"}} {prob:.3f}')
        except Exception:
            pass

    # ── NEW: Macro data metrics ──
    macro_file = "/app/config/macro_data.json"
    if os.path.exists(macro_file):
        try:
            with open(macro_file) as f:
                macro = json.load(f)
            latest = macro.get("latest", {})
            for name, val in latest.items():
                if isinstance(val, (int, float)):
                    safe_name = name.lower().replace(" ", "_")
                    lines.append(f'aegis_macro_{safe_name} {val}')
        except Exception:
            pass

    # ── NEW: Enrichment source health ──
    enrich_file = "/app/config/enrichment_data.json"
    if os.path.exists(enrich_file):
        try:
            with open(enrich_file) as f:
                enrich = json.load(f)
            ok = len(enrich.get("sources_available", []))
            fail = len(enrich.get("sources_failed", []))
            lines.append(f"aegis_enrichment_sources_ok {ok}")
            lines.append(f"aegis_enrichment_sources_failed {fail}")
            lines.append(f"aegis_enrichment_quotes {len(enrich.get('quotes', {}))}")
        except Exception:
            pass

    # ── NEW: Portfolio allocation metrics ──
    alloc_file = "/app/data/portfolio_allocation.json"
    if os.path.exists(alloc_file):
        try:
            with open(alloc_file) as f:
                alloc = json.load(f)
            for strategy, weight in alloc.get("weights", {}).items():
                safe_name = strategy.lower().replace(" ", "_")
                lines.append(f'aegis_portfolio_weight{{strategy="{safe_name}"}} {weight:.4f}')
        except Exception:
            pass

    # ── NEW: Redis memory ──
    try:
        import redis
        r = redis.Redis(host="aegis-redis", port=6379, password=os.environ.get("REDIS_PASSWORD", ""))
        info = r.info("memory")
        used = info.get("used_memory", 0)
        maxmem = info.get("maxmemory", 268435456)  # 256MB default
        lines.append(f"aegis_redis_used_bytes {used}")
        lines.append(f"aegis_redis_max_bytes {maxmem}")
        if maxmem > 0:
            lines.append(f"aegis_redis_usage_pct {used / maxmem * 100:.1f}")
    except Exception:
        pass

    return "\n".join(lines) + "\n"


def _refresh_loop():
    global _cached_metrics, _last_refresh
    while True:
        try:
            _cached_metrics = _collect_metrics()
            _last_refresh = time.time()
        except Exception:
            pass
        time.sleep(METRICS_INTERVAL)


class MetricsHandler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path == "/metrics":
            self.send_response(200)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.end_headers()
            self.wfile.write(_cached_metrics.encode())
        else:
            self.send_response(404)
            self.end_headers()

    def log_message(self, format, *args):
        pass  # Suppress access logs


def start():
    """Start metrics server in a daemon thread."""
    t = threading.Thread(target=_refresh_loop, daemon=True)
    t.start()
    server = http.server.HTTPServer(("0.0.0.0", PORT), MetricsHandler)
    st = threading.Thread(target=server.serve_forever, daemon=True)
    st.start()
    return server


if __name__ == "__main__":
    start()
    print(f"Metrics server on :{PORT}/metrics")
    while True:
        time.sleep(3600)
