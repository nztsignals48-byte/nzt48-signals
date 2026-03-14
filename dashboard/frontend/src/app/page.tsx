'use client'

import { useState, useEffect, useCallback, useRef } from 'react'
import clsx from 'clsx'
import { getAPI, getWSURL, fetchAPI, fmt, fmtDollar, fmtPct, timeAgo } from './lib/api'
import { getRegimeColor, getDDColor, getScoreColor, getScoreBorder } from './lib/colors'

// ─── Types ────────────────────────────────────────────────────────────────────
interface Signal {
  id: string; ticker: string; direction: string; strategy: string
  confidence: number; entry: number; stop: number; target_1r: number
  regime: string; status: string; timestamp: string; rvol: number
}
interface Position {
  id: string; ticker: string; direction: string; entry: number
  current_price: number; shares: number; unrealised_pnl: number
  unrealised_r: number; ladder_rung: number; current_stop: number
  strategy: string; entry_time: string; peak_r: number
}
interface Performance {
  total_pnl: number; total_trades: number; win_count: number
  loss_count: number; win_rate: number
}

// ─── Colour helpers ────────────────────────────────────────────────────────────
function dirColour(d: string) {
  if (d === 'LONG' || d === 'BULL') return 'text-green-400'
  if (d === 'SHORT' || d === 'BEAR') return 'text-red-400'
  return 'text-nzt-muted'
}
function confidenceColour(c: number) {
  if (c >= 70) return 'text-green-400'
  if (c >= 55) return 'text-amber-400'
  return 'text-red-400'
}
function exitColour(score: number) {
  if (score >= 86) return 'text-red-400 font-bold'
  if (score >= 51) return 'text-amber-400'
  return 'text-green-400'
}
function exitLabel(score: number) {
  if (score >= 86) return 'EXIT NOW'
  if (score >= 51) return 'REDUCE'
  if (score >= 31) return 'TRAIL'
  return 'HOLD'
}

// ─── Sub-components ────────────────────────────────────────────────────────────

// Wiring dot: green/amber/red
function WiringDot({ label, status }: { label: string; status: string | undefined }) {
  const s = (status || '').toUpperCase()
  const colour = s === 'OK' || s === 'PASS' || s === 'CONNECTED' || s === 'ACTIVE'
    ? 'bg-green-500'
    : s === 'WARN' || s === 'DEGRADED'
    ? 'bg-amber-500'
    : s === 'ERROR' || s === 'FAIL' || s === 'DISCONNECTED'
    ? 'bg-red-500 animate-pulse'
    : 'bg-nzt-muted'
  return (
    <div className="flex items-center gap-1.5 px-2">
      <span className={clsx('w-2 h-2 rounded-full flex-shrink-0', colour)} />
      <span className="text-xs text-nzt-muted uppercase tracking-wide">{label}</span>
    </div>
  )
}

// Stat tile for the key-metrics row
function StatTile({ label, value, sub, colour }: { label: string; value: string; sub?: string; colour?: string }) {
  return (
    <div className="flex flex-col justify-center px-4 py-2 border-r border-nzt-border last:border-r-0">
      <span className="text-[10px] text-nzt-muted uppercase tracking-widest mb-0.5">{label}</span>
      <span className={clsx('text-xl font-bold leading-none', colour ?? 'text-white')}>{value}</span>
      {sub && <span className="text-[10px] text-nzt-muted mt-0.5">{sub}</span>}
    </div>
  )
}

// Panel card with header
function Panel({ title, badge, children, className }: {
  title: string; badge?: React.ReactNode; children: React.ReactNode; className?: string
}) {
  return (
    <div className={clsx('bg-nzt-card border border-nzt-border rounded-lg flex flex-col overflow-hidden', className)}>
      <div className="flex items-center justify-between px-3 py-2 border-b border-nzt-border flex-shrink-0">
        <span className="text-[10px] font-semibold tracking-widest uppercase text-nzt-muted">{title}</span>
        {badge}
      </div>
      <div className="flex-1 overflow-y-auto min-h-0">
        {children}
      </div>
    </div>
  )
}

// Empty state helper
function Empty({ msg }: { msg: string }) {
  return <div className="flex items-center justify-center h-full text-nzt-muted text-xs py-6">{msg}</div>
}

// ─── Main Component ────────────────────────────────────────────────────────────
export default function Dashboard() {
  // Core state
  const [signals, setSignals] = useState<Signal[]>([])
  const [positions, setPositions] = useState<Position[]>([])
  const [regime, setRegime] = useState<any>(null)
  const [performance, setPerformance] = useState<Performance | null>(null)
  const [connected, setConnected] = useState(false)
  const [lastUpdate, setLastUpdate] = useState<string>('')

  // Panel data
  const [scanHealth, setScanHealth] = useState<any>(null)
  const [opportunity, setOpportunity] = useState<any>(null)
  const [exitScores, setExitScores] = useState<any>(null)
  const [telegramEvents, setTelegramEvents] = useState<any>(null)
  const [drawdown, setDrawdown] = useState<any>(null)
  const [systemWiring, setSystemWiring] = useState<any>(null)
  const [alerts, setAlerts] = useState<any>(null)
  const [todaysPlay, setTodaysPlay] = useState<any>(null)
  const [sectorRotation, setSectorRotation] = useState<any>(null)
  const [compoundingProgress, setCompoundingProgress] = useState<any>(null)
  const [gateStatus, setGateStatus] = useState<any>(null)
  const [consistency, setConsistency] = useState<any>(null)

  // Copilot
  const [copilotQuery, setCopilotQuery] = useState('')
  const [copilotResponse, setCopilotResponse] = useState<any>(null)
  const [copilotLoading, setCopilotLoading] = useState(false)

  // Clock
  const [clock, setClock] = useState('')
  useEffect(() => {
    const tick = () => setClock(new Date().toLocaleTimeString('en-GB', { timeZone: 'UTC', hour: '2-digit', minute: '2-digit', second: '2-digit' }) + ' UTC')
    tick()
    const t = setInterval(tick, 1000)
    return () => clearInterval(t)
  }, [])

  // Core refresh (5s) — signals, positions, regime, perf, scan health, gate
  const refresh = useCallback(async () => {
    try {
      const [sigs, pos, reg, perf, sh, gate, cons] = await Promise.all([
        fetchAPI<Signal[]>('/api/signals?hours=24&limit=20').catch(() => []),
        fetchAPI<Position[]>('/api/virtual-positions').catch(() => []),
        fetchAPI<any>('/api/regime').catch(() => null),
        fetchAPI<any>('/api/performance').catch(() => null),
        fetchAPI<any>('/api/scan_health').catch(() => null),
        fetchAPI<any>('/api/gate').catch(() => null),
        fetchAPI<any>('/api/consistency').catch(() => null),
      ])
      setSignals(Array.isArray(sigs) ? sigs : [])
      setPositions(Array.isArray(pos) ? pos : [])
      setRegime(reg?.current ?? reg ?? null)
      setPerformance(perf?.aggregate ?? perf ?? null)
      setScanHealth(sh)
      setGateStatus(gate)
      setConsistency(cons)
      setLastUpdate(new Date().toLocaleTimeString('en-GB'))
      setConnected(true)
    } catch {
      setConnected(false)
    }
  }, [])

  // Extended refresh (15s) — opportunity, exits, telegram, wiring, sector, compounding, today's play
  const refreshExtended = useCallback(async () => {
    const results = await Promise.allSettled([
      fetchAPI<any>('/api/opportunity'),
      fetchAPI<any>('/api/exits'),
      fetchAPI<any>('/api/telegram/events'),
      fetchAPI<any>('/api/drawdown-status'),
      fetchAPI<any>('/api/system-wiring'),
      fetchAPI<any>('/api/alerts'),
      fetchAPI<any>('/api/todays-play').catch(() => null),
      fetchAPI<any>('/api/sector-rotation').catch(() => null),
      fetchAPI<any>('/api/compounding/progress').catch(() => null),
    ])
    const get = (i: number) => results[i].status === 'fulfilled' ? (results[i] as any).value : null
    setOpportunity(get(0))
    setExitScores(get(1))
    setTelegramEvents(get(2))
    setDrawdown(get(3))
    setSystemWiring(get(4))
    setAlerts(get(5))
    setTodaysPlay(get(6))
    setSectorRotation(get(7))
    setCompoundingProgress(get(8))
  }, [])

  useEffect(() => {
    refresh(); refreshExtended()
    const i1 = setInterval(refresh, 5000)
    const i2 = setInterval(refreshExtended, 15000)
    return () => { clearInterval(i1); clearInterval(i2) }
  }, [refresh, refreshExtended])

  // WebSocket
  useEffect(() => {
    let ws: WebSocket | null = null
    let reconnectTimer: ReturnType<typeof setTimeout> | null = null
    let attempts = 0, unmounted = false
    function connect() {
      if (unmounted) return
      try {
        ws = new WebSocket(getWSURL())
        ws.onmessage = (e) => { try { const m = JSON.parse(e.data); if (['NEW_SIGNAL','POSITION_UPDATE','REGIME_CHANGE','STATE_UPDATE'].includes(m.type)) refresh() } catch {} }
        ws.onopen = () => { setConnected(true); attempts = 0 }
        ws.onclose = () => { setConnected(false); if (!unmounted) { reconnectTimer = setTimeout(connect, Math.min(1000 * Math.pow(2, attempts++), 30000)) } }
        ws.onerror = () => ws?.close()
      } catch {}
    }
    connect()
    return () => { unmounted = true; if (reconnectTimer) clearTimeout(reconnectTimer); ws?.close() }
  }, [refresh])

  // Copilot submit
  async function submitCopilot(q?: string) {
    const query = q ?? copilotQuery
    if (!query.trim()) return
    setCopilotLoading(true); setCopilotResponse(null)
    try {
      const res = await fetchAPI<any>('/api/copilot/query')
        .catch(() => null)
      // POST
      const r = await fetch(`${getAPI()}/api/copilot/query`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ query }),
      })
      const data = await r.json()
      setCopilotResponse(data)
    } catch (e) {
      setCopilotResponse({ answer: 'Copilot unavailable. Check engine connection.' })
    } finally {
      setCopilotLoading(false)
    }
  }

  // Derived
  const regimeName = regime?.regime ?? regime?.name ?? 'UNKNOWN'
  const regimeVix = regime?.vix ?? 0
  const ddLevel = drawdown?.level ?? 'GREEN'
  const pnlToday = performance?.total_pnl ?? 0
  const winRate = performance?.win_rate ?? 0
  const totalTrades = performance?.total_trades ?? 0
  const p0Alerts = (alerts?.p0 ?? []).length + (alerts?.p1 ?? []).length
  const gatePass = gateStatus?.passed ?? 0
  const gateTotal = gateStatus?.total ?? 8
  const consistencyOk = consistency?.consistent ?? true

  // Compounding
  const equity = compoundingProgress?.current_equity ?? 10000
  const targetEquity = compoundingProgress?.target_equity ?? 10000
  const tradingDay = compoundingProgress?.trading_day ?? 0

  return (
    <div className="flex flex-col bg-nzt-bg text-nzt-text" style={{ height: '100vh', overflow: 'hidden' }}>

      {/* ── HEADER BAR ────────────────────────────────────────────────────────── */}
      <header className="flex-shrink-0 flex items-center justify-between px-4 bg-nzt-card border-b border-nzt-border" style={{ height: 52 }}>
        {/* Left: brand + status */}
        <div className="flex items-center gap-3">
          <span className="text-base font-bold text-white">NZT-48</span>
          <span className={clsx('w-2 h-2 rounded-full', connected ? 'bg-green-500' : 'bg-red-500 animate-pulse')} />
          <span className="text-xs text-nzt-muted">PAPER</span>
          <span className="text-xs text-nzt-muted hidden sm:block">|</span>
          <span className="text-xs text-nzt-muted hidden sm:block">{clock}</span>
        </div>

        {/* Centre: key metrics strip */}
        <div className="flex items-stretch divide-x divide-nzt-border border-x border-nzt-border">
          <StatTile
            label="Regime"
            value={regimeName}
            sub={`VIX ${fmt(regimeVix, 1)}`}
            colour={getRegimeColor(regimeName)}
          />
          <StatTile
            label="P&L Today"
            value={fmtDollar(pnlToday)}
            sub={`${totalTrades} trades`}
            colour={pnlToday >= 0 ? 'text-green-400' : 'text-red-400'}
          />
          <StatTile
            label="Win Rate"
            value={fmtPct(winRate)}
            sub={`${performance?.win_count ?? 0}W / ${performance?.loss_count ?? 0}L`}
            colour={winRate >= 50 ? 'text-green-400' : 'text-amber-400'}
          />
          <StatTile
            label="Drawdown"
            value={ddLevel}
            sub={`-${fmt(drawdown?.current_drawdown_pct ?? 0, 2)}%`}
            colour={getDDColor(ddLevel)}
          />
          <StatTile
            label="Equity"
            value={`£${Math.round(equity).toLocaleString()}`}
            sub={`Day ${tradingDay} / 252`}
            colour="text-white"
          />
          <StatTile
            label="Gate"
            value={`${gatePass}/${gateTotal}`}
            sub={gateStatus?.ready ? 'GO' : 'NO-GO'}
            colour={gateStatus?.ready ? 'text-green-400' : 'text-red-400'}
          />
        </div>

        {/* Right: scan health + alert count + kill switch */}
        <div className="flex items-center gap-3">
          {p0Alerts > 0 && (
            <span className="text-xs font-bold text-red-400 animate-pulse border border-red-500/50 rounded px-2 py-0.5">
              ⚠ {p0Alerts} ALERT{p0Alerts > 1 ? 'S' : ''}
            </span>
          )}
          <div className="text-right">
            <div className="text-[10px] text-nzt-muted uppercase tracking-wide">Last scan</div>
            <div className={clsx('text-xs font-mono', scanHealth?.state === 'OK' ? 'text-green-400' : 'text-amber-400')}>
              {scanHealth?.state ?? '—'} · {timeAgo(scanHealth?.last_success_ts)}
            </div>
          </div>
          <button
            className="bg-red-900/80 hover:bg-red-700 border border-red-600 text-red-200 text-xs font-bold px-3 py-1.5 rounded uppercase tracking-wide transition-colors"
            onClick={() => {
              if (confirm('Activate KILL SWITCH? This will halt all trading.')) {
                fetch(`${getAPI()}/api/kill-switch`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ activate: true }) })
              }
            }}
          >Kill Switch</button>
        </div>
      </header>

      {/* ── WIRING BAR ─────────────────────────────────────────────────────────── */}
      <div className="flex-shrink-0 flex items-center gap-1 px-3 bg-[#0d0d0d] border-b border-nzt-border" style={{ height: 32 }}>
        <span className="text-[10px] text-nzt-muted uppercase tracking-widest mr-2">System</span>
        {['data_hub','engine','artifacts','telegram','pdf','learning','scheduler'].map(key => (
          <WiringDot key={key} label={key.replace('_',' ')} status={systemWiring?.components?.[key]?.status ?? systemWiring?.[key]?.status} />
        ))}
        {!consistencyOk && (
          <span className="ml-auto text-[10px] text-amber-400 font-semibold">⚠ CONSISTENCY ISSUE</span>
        )}
        <span className="ml-auto text-[10px] text-nzt-muted">Updated {lastUpdate || '—'}</span>
      </div>

      {/* ── ALERT BANNER (P0 only) ──────────────────────────────────────────────── */}
      {(alerts?.p0 ?? []).length > 0 && (
        <div className="flex-shrink-0 bg-red-950/80 border-b border-red-700 px-4 py-1.5 flex items-center gap-2">
          <span className="text-red-400 text-xs font-bold uppercase animate-pulse">● P0 CRITICAL</span>
          {(alerts.p0 as string[]).map((a: string, i: number) => (
            <span key={i} className="text-red-300 text-xs">{a}</span>
          ))}
        </div>
      )}

      {/* ── 3-COLUMN MAIN BODY ─────────────────────────────────────────────────── */}
      <main className="flex-1 grid min-h-0 gap-2 p-2" style={{ gridTemplateColumns: '1fr 1fr 1fr' }}>

        {/* ── LEFT COLUMN ──────────────────────────────────────────────────────── */}
        <div className="flex flex-col gap-2 min-h-0">

          {/* TODAY'S #1 TRADE */}
          <Panel
            title="Today's Play"
            badge={
              <span className={clsx(
                'text-[10px] font-bold px-2 py-0.5 rounded uppercase',
                todaysPlay?.lane === 'TRADE' ? 'bg-green-900/50 text-green-400' :
                todaysPlay?.lane === 'WATCH' ? 'bg-amber-900/50 text-amber-400' :
                'bg-nzt-border text-nzt-muted'
              )}>
                {todaysPlay?.lane ?? 'NO LANE'}
              </span>
            }
            className="flex-shrink-0"
            style={{ minHeight: 140, maxHeight: 180 } as any}
          >
            {todaysPlay?.ticker ? (
              <div className="px-3 py-2 flex items-start justify-between gap-3">
                <div className="flex-1">
                  <div className="flex items-baseline gap-2 mb-1">
                    <span className="text-2xl font-bold text-white">{todaysPlay.ticker}</span>
                    <span className={clsx('text-sm font-semibold', dirColour(todaysPlay.direction ?? ''))}>
                      {todaysPlay.direction}
                    </span>
                    <span className="text-xs text-nzt-muted">{todaysPlay.strategy}</span>
                  </div>
                  <div className="grid grid-cols-3 gap-x-4 gap-y-0.5 text-xs">
                    <span className="text-nzt-muted">Entry</span>
                    <span className="text-nzt-muted">Stop</span>
                    <span className="text-nzt-muted">Target</span>
                    <span className="text-white font-mono">{fmt(todaysPlay.entry, 3)}</span>
                    <span className="text-red-400 font-mono">{fmt(todaysPlay.stop, 3)}</span>
                    <span className="text-green-400 font-mono">{fmt(todaysPlay.target_1r, 3)}</span>
                  </div>
                  {todaysPlay.sector_alert && (
                    <div className="mt-1 text-[10px] text-amber-400">▲ {todaysPlay.sector_alert}</div>
                  )}
                </div>
                <div className={clsx('flex flex-col items-center justify-center w-14 h-14 rounded-full border-2 flex-shrink-0', getScoreBorder(todaysPlay.play_score ?? 0))}>
                  <span className={clsx('text-xl font-bold', getScoreColor(todaysPlay.play_score ?? 0))}>{todaysPlay.play_score ?? 0}</span>
                  <span className="text-[9px] text-nzt-muted">/100</span>
                </div>
              </div>
            ) : (
              <div className="px-3 py-3">
                <div className="flex items-center gap-2 mb-1">
                  <span className="text-xs font-bold text-red-400 uppercase">No Trade Today</span>
                  <span className="text-xs text-nzt-muted">ABSTAIN</span>
                </div>
                <p className="text-xs text-nzt-muted">{todaysPlay?.reason ?? 'All candidates failed qualification gates.'}</p>
              </div>
            )}
          </Panel>

          {/* OPEN POSITIONS */}
          <Panel
            title={`Open Positions (${positions.length})`}
            className="flex-1"
          >
            {positions.length === 0 ? (
              <Empty msg="No open positions" />
            ) : (
              <div className="divide-y divide-nzt-border">
                {positions.map(p => (
                  <div key={p.id} className="px-3 py-2 flex items-center justify-between gap-2">
                    <div className="flex-1 min-w-0">
                      <div className="flex items-center gap-2">
                        <span className="text-sm font-bold text-white">{p.ticker}</span>
                        <span className={clsx('text-xs', dirColour(p.direction))}>{p.direction}</span>
                        <span className="text-[10px] text-nzt-muted">{p.strategy}</span>
                      </div>
                      <div className="text-[10px] text-nzt-muted mt-0.5">
                        Entry {fmt(p.entry, 3)} · Stop {fmt(p.current_stop, 3)} · Peak {fmt(p.peak_r, 2)}R
                      </div>
                    </div>
                    <div className="text-right flex-shrink-0">
                      <div className={clsx('text-sm font-bold', p.unrealised_pnl >= 0 ? 'text-green-400' : 'text-red-400')}>
                        {fmtDollar(p.unrealised_pnl)}
                      </div>
                      <div className={clsx('text-[10px]', p.unrealised_r >= 0 ? 'text-green-400' : 'text-red-400')}>
                        {fmt(p.unrealised_r, 2)}R
                      </div>
                    </div>
                  </div>
                ))}
              </div>
            )}
          </Panel>

          {/* EXIT SCORES */}
          <Panel
            title="Exit Scores"
            className="flex-shrink-0"
            style={{ minHeight: 100, maxHeight: 180 } as any}
          >
            {!(exitScores?.positions ?? []).length ? (
              <Empty msg="No positions to score" />
            ) : (
              <div className="divide-y divide-nzt-border">
                {(exitScores.positions as any[]).slice(0, 4).map((p: any, i: number) => (
                  <div key={i} className="px-3 py-1.5 flex items-center justify-between">
                    <span className="text-xs font-bold text-white">{p.ticker}</span>
                    <div className="flex items-center gap-3">
                      <div className="w-20 h-1.5 bg-nzt-border rounded-full overflow-hidden">
                        <div className={clsx('h-full rounded-full', p.exit_score >= 86 ? 'bg-red-500' : p.exit_score >= 51 ? 'bg-amber-500' : 'bg-green-500')} style={{ width: `${p.exit_score}%` }} />
                      </div>
                      <span className={clsx('text-xs font-bold w-20 text-right', exitColour(p.exit_score ?? 0))}>
                        {exitLabel(p.exit_score ?? 0)}
                      </span>
                    </div>
                  </div>
                ))}
              </div>
            )}
          </Panel>
        </div>

        {/* ── CENTRE COLUMN ────────────────────────────────────────────────────── */}
        <div className="flex flex-col gap-2 min-h-0">

          {/* OPPORTUNITY LANE */}
          <Panel
            title="Opportunity Lane (+2% Net)"
            badge={
              <span className="text-[10px] text-nzt-muted">
                {opportunity?.qualified_count ?? 0} qualified · {opportunity?.borderline_count ?? 0} borderline
              </span>
            }
            className="flex-1"
          >
            {!(opportunity?.candidates ?? []).length ? (
              <Empty msg="No candidates yet this cycle" />
            ) : (
              <div className="divide-y divide-nzt-border">
                {(opportunity.candidates as any[]).slice(0, 8).map((c: any, i: number) => (
                  <div key={i} className={clsx(
                    'px-3 py-2 flex items-center justify-between gap-2',
                    c.decision === 'QUALIFIED' ? 'bg-green-950/20' : c.decision === 'BORDERLINE' ? 'bg-amber-950/20' : ''
                  )}>
                    <div className="flex-1 min-w-0">
                      <div className="flex items-center gap-2">
                        <span className="text-sm font-bold text-white">{c.ticker}</span>
                        <span className={clsx('text-[10px] font-semibold uppercase',
                          c.decision === 'QUALIFIED' ? 'text-green-400' :
                          c.decision === 'BORDERLINE' ? 'text-amber-400' : 'text-nzt-muted'
                        )}>{c.decision}</span>
                      </div>
                      {c.gate_fail_reason && (
                        <div className="text-[10px] text-nzt-muted truncate">{c.gate_fail_reason}</div>
                      )}
                    </div>
                    <div className="flex items-center gap-2 flex-shrink-0">
                      <div className="text-right">
                        <div className={clsx('text-sm font-bold', confidenceColour(c.feasibility_score ?? c.confidence ?? 0))}>
                          {Math.round(c.feasibility_score ?? c.confidence ?? 0)}
                        </div>
                        <div className="text-[10px] text-nzt-muted">score</div>
                      </div>
                    </div>
                  </div>
                ))}
              </div>
            )}
          </Panel>

          {/* RECENT SIGNALS */}
          <Panel
            title={`Recent Signals (${signals.length})`}
            className="flex-1"
          >
            {signals.length === 0 ? (
              <Empty msg="No signals in last 24h" />
            ) : (
              <div className="divide-y divide-nzt-border">
                {signals.slice(0, 10).map(s => (
                  <div key={s.id} className="px-3 py-1.5 flex items-center justify-between gap-2">
                    <div className="flex items-center gap-2 flex-1 min-w-0">
                      <span className="text-xs font-bold text-white w-16 truncate">{s.ticker}</span>
                      <span className={clsx('text-[10px] font-semibold', dirColour(s.direction))}>{s.direction}</span>
                      <span className="text-[10px] text-nzt-muted">{s.strategy}</span>
                    </div>
                    <div className="flex items-center gap-3 flex-shrink-0">
                      <span className={clsx('text-xs font-bold', confidenceColour(s.confidence))}>{s.confidence}%</span>
                      <span className="text-[10px] text-nzt-muted w-16 text-right">{timeAgo(s.timestamp)}</span>
                    </div>
                  </div>
                ))}
              </div>
            )}
          </Panel>

          {/* SECTOR ROTATION */}
          <Panel
            title="Sector Rotation"
            className="flex-shrink-0"
            style={{ minHeight: 120, maxHeight: 180 } as any}
          >
            {!(sectorRotation?.sectors ?? []).length ? (
              <Empty msg="No sector data" />
            ) : (
              <div className="divide-y divide-nzt-border">
                {(sectorRotation.sectors as any[]).slice(0, 5).map((s: any, i: number) => (
                  <div key={i} className="px-3 py-1 flex items-center gap-3">
                    <span className="text-xs text-white w-28 truncate">{s.name ?? s.sector}</span>
                    <span className={clsx('text-[10px] font-semibold uppercase px-1.5 py-0.5 rounded',
                      s.signal === 'INFLOW' ? 'bg-green-900/40 text-green-400' :
                      s.signal === 'OUTFLOW' ? 'bg-red-900/40 text-red-400' :
                      'bg-nzt-border text-nzt-muted'
                    )}>{s.signal ?? 'NEUTRAL'}</span>
                    <div className="flex-1 h-1 bg-nzt-border rounded-full overflow-hidden">
                      <div className={clsx('h-full rounded-full', s.signal === 'INFLOW' ? 'bg-green-500' : s.signal === 'OUTFLOW' ? 'bg-red-500' : 'bg-nzt-muted')}
                        style={{ width: `${Math.min(s.score ?? 50, 100)}%` }} />
                    </div>
                    <span className="text-xs text-nzt-muted w-12 text-right">{s.leader}</span>
                  </div>
                ))}
              </div>
            )}
          </Panel>
        </div>

        {/* ── RIGHT COLUMN ─────────────────────────────────────────────────────── */}
        <div className="flex flex-col gap-2 min-h-0">

          {/* SCAN ENGINE HEALTH */}
          <Panel
            title="Scan Engine"
            badge={
              <span className={clsx(
                'text-[10px] font-bold px-2 py-0.5 rounded uppercase',
                scanHealth?.state === 'OK' ? 'text-green-400 bg-green-900/30' : 'text-amber-400 bg-amber-900/30'
              )}>{scanHealth?.state ?? '—'}</span>
            }
            className="flex-shrink-0"
            style={{ minHeight: 90, maxHeight: 130 } as any}
          >
            <div className="grid grid-cols-3 gap-0 divide-x divide-nzt-border px-1 py-2">
              {[
                ['Ticks', scanHealth?.tick_count ?? '—'],
                ['Runs', scanHealth?.engine_runs_today ?? '—'],
                ['Signals', scanHealth?.signals_emitted_today ?? '—'],
              ].map(([l, v]) => (
                <div key={l} className="flex flex-col items-center py-1">
                  <span className="text-lg font-bold text-white">{v}</span>
                  <span className="text-[10px] text-nzt-muted uppercase">{l}</span>
                </div>
              ))}
            </div>
            <div className="px-3 pb-2 text-[10px] text-nzt-muted">
              Last success: <span className="text-white">{timeAgo(scanHealth?.last_success_ts)}</span>
              {scanHealth?.drought_state && scanHealth.drought_state !== 'NONE' && (
                <span className="ml-2 text-amber-400 font-semibold uppercase">⚠ Drought: {scanHealth.drought_state}</span>
              )}
            </div>
          </Panel>

          {/* TELEGRAM DESK */}
          <Panel
            title="Telegram Desk"
            badge={
              <span className="text-[10px] text-nzt-muted">
                {(telegramEvents?.events ?? []).length} events
              </span>
            }
            className="flex-1"
          >
            {!(telegramEvents?.events ?? []).length ? (
              <Empty msg="No Telegram events today" />
            ) : (
              <div className="divide-y divide-nzt-border">
                {(telegramEvents.events as any[]).slice(0, 8).map((e: any, i: number) => (
                  <div key={i} className="px-3 py-1.5 flex items-start gap-2">
                    <span className={clsx(
                      'text-[9px] font-bold px-1.5 py-0.5 rounded flex-shrink-0 uppercase mt-0.5',
                      e.action === 'SENT' ? 'bg-green-900/40 text-green-400' :
                      e.action === 'SUPPRESSED' ? 'bg-amber-900/40 text-amber-400' :
                      'bg-nzt-border text-nzt-muted'
                    )}>{e.label ?? e.category ?? e.action}</span>
                    <span className="text-xs text-nzt-muted truncate flex-1">{e.message ?? e.text}</span>
                    <span className="text-[10px] text-nzt-muted flex-shrink-0">{timeAgo(e.timestamp ?? e.ts)}</span>
                  </div>
                ))}
              </div>
            )}
          </Panel>

          {/* OPERATOR COPILOT */}
          <Panel
            title="Operator Copilot"
            className="flex-shrink-0"
            style={{ minHeight: 140, maxHeight: 220 } as any}
          >
            <div className="px-3 py-2 flex flex-col gap-2">
              <div className="flex gap-1 flex-wrap">
                {['Why no signal?', 'Top candidates', 'Health summary', 'What changed?'].map(q => (
                  <button key={q}
                    className="text-[10px] px-2 py-1 bg-nzt-border hover:bg-nzt-border/80 rounded text-nzt-muted hover:text-white transition-colors"
                    onClick={() => { setCopilotQuery(q); submitCopilot(q) }}
                  >{q}</button>
                ))}
              </div>
              <div className="flex gap-2">
                <input
                  className="flex-1 bg-nzt-bg border border-nzt-border rounded px-2 py-1 text-xs text-white placeholder-nzt-muted focus:outline-none focus:border-nzt-accent"
                  placeholder="Ask the copilot..."
                  value={copilotQuery}
                  onChange={e => setCopilotQuery(e.target.value)}
                  onKeyDown={e => e.key === 'Enter' && submitCopilot()}
                />
                <button
                  className="bg-nzt-accent/20 hover:bg-nzt-accent/30 border border-nzt-accent/50 text-nzt-accent text-xs px-3 py-1 rounded transition-colors"
                  onClick={() => submitCopilot()}
                  disabled={copilotLoading}
                >{copilotLoading ? '…' : 'ASK'}</button>
              </div>
              {copilotResponse && (
                <div className="text-xs text-nzt-muted bg-nzt-bg rounded p-2 border border-nzt-border max-h-16 overflow-y-auto">
                  {copilotResponse.answer}
                </div>
              )}
            </div>
          </Panel>

          {/* GO-LIVE GATE MINI */}
          <Panel
            title="Go-Live Gate"
            badge={
              <a href="/gate" className="text-[10px] text-nzt-accent hover:underline">Full view →</a>
            }
            className="flex-shrink-0"
            style={{ minHeight: 100, maxHeight: 160 } as any}
          >
            <div className="px-3 py-2">
              <div className="flex items-center gap-2 mb-2">
                <span className={clsx('text-base font-bold', gateStatus?.ready ? 'text-green-400' : 'text-red-400')}>
                  {gateStatus?.ready ? '✓ GO' : '✗ NO-GO'}
                </span>
                <span className="text-xs text-nzt-muted">{gatePass}/{gateTotal} checks passed</span>
              </div>
              <div className="grid grid-cols-2 gap-x-3 gap-y-0.5">
                {(gateStatus?.checks ?? []).slice(0, 8).map((c: any, i: number) => (
                  <div key={i} className="flex items-center gap-1.5">
                    <span className={clsx('w-1.5 h-1.5 rounded-full flex-shrink-0', c.passed ? 'bg-green-500' : 'bg-red-500')} />
                    <span className="text-[10px] text-nzt-muted truncate">{c.name}</span>
                  </div>
                ))}
              </div>
            </div>
          </Panel>
        </div>
      </main>
    </div>
  )
}
