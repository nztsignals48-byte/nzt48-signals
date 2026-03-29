"""AEGIS Command Station — Book 173.

Bloomberg-like one-man quant terminal. Web-based dashboard served
via a lightweight Python HTTP server (no dependencies beyond stdlib).

Panels:
  1. PORTFOLIO: Positions, P&L, equity curve, drawdown gauge
  2. SIGNALS: Live signal feed from all 17 generators
  3. RISK: Regime, correlation, heat map, overnight exposure
  4. STRATEGIES: Per-strategy Sharpe, WR, lifecycle state, SPRT
  5. EXECUTION: Fill quality, shortfall, latency, capacity
  6. HEALTH: 15-check watchdog status, circuit breakers
  7. JOURNAL: System memory, lessons, incidents
  8. COMMAND: Input box for operator commands

Access: http://localhost:8173 (or EC2 IP:8173)

Usage:
    python -m python_brain.terminal.command_station --port 8173
"""

from __future__ import annotations

import json
import logging
import os
import time
from datetime import datetime, timezone
from http.server import HTTPServer, BaseHTTPRequestHandler
from pathlib import Path
from typing import Any, Dict

log = logging.getLogger("command_station")

_PROJECT_ROOT = Path(os.environ.get("AEGIS_ROOT", Path(__file__).resolve().parents[2]))
DATA_DIR = Path(os.environ.get("AEGIS_DATA_DIR", _PROJECT_ROOT / "data"))


def _load_json_file(path: Path) -> dict:
    """Load a JSON file, returning empty dict on failure."""
    try:
        if path.exists():
            with open(path) as f:
                return json.load(f)
    except (json.JSONDecodeError, IOError):
        pass
    return {}


def _collect_system_state() -> Dict[str, Any]:
    """Collect current system state from all data files."""
    state = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "health": _load_json_file(DATA_DIR / "health_status.json"),
        "recommendations": _load_json_file(DATA_DIR / "ouroboros_recommendations.json"),
        "persistent_memory": _load_json_file(DATA_DIR / "persistent_memory.json"),
        "system_memory": _load_json_file(DATA_DIR / "system_memory.json"),
        "feature_flags": _load_json_file(DATA_DIR / "feature_flags.json"),
    }

    # Load latest regime report
    regime_dir = DATA_DIR / "regime_reports"
    if regime_dir.exists():
        reports = sorted(regime_dir.glob("*.json"))
        if reports:
            state["regime"] = _load_json_file(reports[-1])

    # Load latest MFE/MAE report
    forensics_dir = DATA_DIR / "forensics"
    if forensics_dir.exists():
        mfe_files = sorted(forensics_dir.glob("mfe_mae_*.json"))
        if mfe_files:
            state["mfe_mae"] = _load_json_file(mfe_files[-1])

    return state


# HTML template for the command station
TERMINAL_HTML = """<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>AEGIS V2 Command Station</title>
<style>
  * { margin: 0; padding: 0; box-sizing: border-box; }
  body { background: #0a0a0a; color: #00ff41; font-family: 'Courier New', monospace; font-size: 13px; }
  .header { background: #111; padding: 8px 16px; border-bottom: 1px solid #333;
            display: flex; justify-content: space-between; align-items: center; }
  .header h1 { color: #00ff41; font-size: 18px; letter-spacing: 2px; }
  .header .status { color: #666; font-size: 11px; }
  .grid { display: grid; grid-template-columns: 1fr 1fr 1fr; gap: 2px; padding: 2px; height: calc(100vh - 40px); }
  .panel { background: #111; border: 1px solid #222; padding: 8px; overflow-y: auto; }
  .panel h2 { color: #ff6600; font-size: 14px; margin-bottom: 8px; border-bottom: 1px solid #333; padding-bottom: 4px; }
  .metric { display: flex; justify-content: space-between; padding: 2px 0; }
  .metric .label { color: #888; }
  .metric .value { color: #00ff41; font-weight: bold; }
  .metric .value.negative { color: #ff3333; }
  .metric .value.warning { color: #ffaa00; }
  .metric .value.good { color: #00ff41; }
  table { width: 100%; border-collapse: collapse; font-size: 12px; }
  th { color: #ff6600; text-align: left; padding: 3px; border-bottom: 1px solid #333; }
  td { padding: 3px; border-bottom: 1px solid #1a1a1a; }
  .signal-feed { max-height: 300px; overflow-y: auto; }
  .signal { padding: 2px 4px; border-left: 2px solid #00ff41; margin: 2px 0; font-size: 11px; }
  .signal.rejected { border-color: #ff3333; color: #666; }
  .cmd-input { width: 100%; background: #0a0a0a; color: #00ff41; border: 1px solid #333;
               padding: 6px; font-family: inherit; font-size: 13px; }
  .cmd-input:focus { outline: none; border-color: #00ff41; }
  #live-clock { color: #00ff41; }
</style>
</head>
<body>
<div class="header">
  <h1>AEGIS V2 COMMAND STATION</h1>
  <div class="status">
    <span id="live-clock"></span> |
    <span id="conn-status">LOADING...</span>
  </div>
</div>
<div class="grid">

  <!-- PANEL 1: Portfolio -->
  <div class="panel" id="portfolio-panel">
    <h2>PORTFOLIO</h2>
    <div id="portfolio-data">Loading...</div>
  </div>

  <!-- PANEL 2: Signals -->
  <div class="panel" id="signals-panel">
    <h2>SIGNAL FEED</h2>
    <div class="signal-feed" id="signal-feed">Waiting for signals...</div>
  </div>

  <!-- PANEL 3: Risk -->
  <div class="panel" id="risk-panel">
    <h2>RISK</h2>
    <div id="risk-data">Loading...</div>
  </div>

  <!-- PANEL 4: Strategies -->
  <div class="panel" id="strategies-panel">
    <h2>STRATEGIES</h2>
    <div id="strategies-data">Loading...</div>
  </div>

  <!-- PANEL 5: Health -->
  <div class="panel" id="health-panel">
    <h2>SYSTEM HEALTH</h2>
    <div id="health-data">Loading...</div>
  </div>

  <!-- PANEL 6: Command -->
  <div class="panel" id="command-panel">
    <h2>COMMAND</h2>
    <input type="text" class="cmd-input" id="cmd-input" placeholder="Enter command..." autofocus>
    <div id="cmd-output" style="margin-top: 8px; color: #888;"></div>
    <div style="margin-top: 12px; color: #444; font-size: 11px;">
      Commands: status, health, signals, regime, positions, strategies, flags, help
    </div>
  </div>

</div>

<script>
function updateClock() {
  document.getElementById('live-clock').textContent = new Date().toISOString().slice(0,19) + 'Z';
}
setInterval(updateClock, 1000);
updateClock();

async function fetchState() {
  try {
    const resp = await fetch('/api/state');
    const state = await resp.json();
    renderPortfolio(state);
    renderRisk(state);
    renderHealth(state);
    renderStrategies(state);
    document.getElementById('conn-status').textContent = 'CONNECTED';
    document.getElementById('conn-status').style.color = '#00ff41';
  } catch(e) {
    document.getElementById('conn-status').textContent = 'DISCONNECTED';
    document.getElementById('conn-status').style.color = '#ff3333';
  }
}

function renderPortfolio(state) {
  const mem = state.persistent_memory || {};
  const recs = state.recommendations || {};
  const html = `
    <div class="metric"><span class="label">Equity</span><span class="value">&pound;${(mem.equity || 10000).toFixed(0)}</span></div>
    <div class="metric"><span class="label">Total Trades</span><span class="value">${mem.total_trades || 0}</span></div>
    <div class="metric"><span class="label">Win Rate</span><span class="value">${((mem.all_time_win_rate || 0) * 100).toFixed(1)}%</span></div>
    <div class="metric"><span class="label">Cost-Adj P&L</span><span class="value ${(mem.cumulative_pnl || 0) < 0 ? 'negative' : 'good'}">&pound;${(mem.cumulative_pnl || 0).toFixed(2)}</span></div>
  `;
  document.getElementById('portfolio-data').innerHTML = html;
}

function renderRisk(state) {
  const regime = state.regime || {};
  const recs = state.recommendations || {};
  const html = `
    <div class="metric"><span class="label">Regime</span><span class="value">${regime.current_regime || 'UNKNOWN'}</span></div>
    <div class="metric"><span class="label">VIX</span><span class="value">${(regime.vix || 0).toFixed(1)}</span></div>
    <div class="metric"><span class="label">HMM State</span><span class="value">${regime.hmm_state || '?'}</span></div>
    <div class="metric"><span class="label">Drawdown</span><span class="value ${(recs.drawdown_pct || 0) > 5 ? 'negative' : 'good'}">${(recs.drawdown_pct || 0).toFixed(1)}%</span></div>
  `;
  document.getElementById('risk-data').innerHTML = html;
}

function renderHealth(state) {
  const health = state.health || {};
  const checks = health.checks || [];
  let html = `<div class="metric"><span class="label">Overall</span><span class="value ${health.healthy ? 'good' : 'negative'}">${health.healthy ? 'HEALTHY' : 'DEGRADED'}</span></div>`;
  const failed = (health.failed || []);
  if (failed.length > 0) {
    html += '<div style="color:#ff3333;margin-top:4px">Failed: ' + failed.join(', ') + '</div>';
  }
  html += `<div class="metric"><span class="label">Checks</span><span class="value">${checks.length} total</span></div>`;
  document.getElementById('health-data').innerHTML = html;
}

function renderStrategies(state) {
  const recs = state.recommendations || {};
  const lifecycle = recs.lifecycle || {};
  let html = '<table><tr><th>Strategy</th><th>State</th><th>WR</th><th>Trades</th></tr>';
  for (const [name, data] of Object.entries(lifecycle)) {
    html += `<tr><td>${name}</td><td>${data.state || '?'}</td><td>${((data.win_rate || 0) * 100).toFixed(0)}%</td><td>${data.trade_count || 0}</td></tr>`;
  }
  html += '</table>';
  document.getElementById('strategies-data').innerHTML = html;
}

// Command input
document.getElementById('cmd-input').addEventListener('keypress', async function(e) {
  if (e.key === 'Enter') {
    const cmd = this.value.trim();
    this.value = '';
    try {
      const resp = await fetch('/api/command', {method: 'POST', body: cmd});
      const result = await resp.text();
      document.getElementById('cmd-output').textContent = result;
    } catch(e) {
      document.getElementById('cmd-output').textContent = 'Error: ' + e.message;
    }
  }
});

// Refresh every 30 seconds
setInterval(fetchState, 30000);
fetchState();
</script>
</body>
</html>"""


class CommandStationHandler(BaseHTTPRequestHandler):
    """HTTP request handler for the command station."""

    def do_GET(self):
        if self.path == "/" or self.path == "/index.html":
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.end_headers()
            self.wfile.write(TERMINAL_HTML.encode())

        elif self.path == "/api/state":
            state = _collect_system_state()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps(state, default=str).encode())

        else:
            self.send_response(404)
            self.end_headers()

    def do_POST(self):
        if self.path == "/api/command":
            length = int(self.headers.get("Content-Length", 0))
            body = self.rfile.read(length).decode() if length > 0 else ""
            response = _handle_command(body)
            self.send_response(200)
            self.send_header("Content-Type", "text/plain")
            self.end_headers()
            self.wfile.write(response.encode())
        else:
            self.send_response(404)
            self.end_headers()

    def log_message(self, format, *args):
        pass  # Suppress access logs


def _handle_command(cmd: str) -> str:
    """Process an operator command."""
    cmd = cmd.strip().lower()

    if cmd == "status":
        state = _collect_system_state()
        mem = state.get("persistent_memory", {})
        return (
            f"Equity: {mem.get('equity', 10000):.0f} GBP\n"
            f"Trades: {mem.get('total_trades', 0)}\n"
            f"WR: {mem.get('all_time_win_rate', 0)*100:.1f}%\n"
            f"Regime: {state.get('regime', {}).get('current_regime', '?')}"
        )

    elif cmd == "health":
        health = _load_json_file(DATA_DIR / "health_status.json")
        if health.get("healthy"):
            return "ALL CHECKS PASS"
        failed = health.get("failed", [])
        return f"FAILED: {', '.join(failed)}" if failed else "No data"

    elif cmd == "flags":
        flags = _load_json_file(DATA_DIR / "feature_flags.json")
        lines = [f"  {k}: {'ON' if v else 'OFF'}" for k, v in sorted(flags.items())]
        return "Feature Flags:\n" + "\n".join(lines)

    elif cmd == "help":
        return "Commands: status, health, signals, regime, positions, strategies, flags"

    return f"Unknown command: {cmd}"


def run_server(port: int = 8173):
    """Start the command station HTTP server."""
    server = HTTPServer(("0.0.0.0", port), CommandStationHandler)
    log.info("COMMAND STATION running on http://0.0.0.0:%d", port)
    print(f"AEGIS V2 Command Station: http://localhost:{port}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        server.shutdown()


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="AEGIS V2 Command Station")
    parser.add_argument("--port", type=int, default=8173)
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO)
    run_server(args.port)
