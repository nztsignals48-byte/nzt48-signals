'use client'

import { useState, useEffect, useCallback, useMemo } from 'react'
import clsx from 'clsx'
import { Card, DirBadge, StatusBadge, BotBadge, TickerLink, NavTabs } from '../lib/components'
import { getAPI, fetchAPI, fmt, fmtDollar, fmtPct, fmtDate, fmtTime, timeAgo } from '../lib/api'
import { getRegimeColor, getDDColor, getGradeColor } from '../lib/colors'

// === Types ===
type Tab = 'equity' | 'journal' | 'strategies' | 'regime' | 'winrate' | 'drawdown'

export default function HistoryPage() {
  const [tab, setTab] = useState<Tab>('equity')
  const [loading, setLoading] = useState(true)
  const [lastUpdate, setLastUpdate] = useState('')

  // Data states
  const [equityCurve, setEquityCurve] = useState<any[]>([])
  const [journal, setJournal] = useState<{ trades: any[]; total: number }>({ trades: [], total: 0 })
  const [strategies, setStrategies] = useState<any[]>([])
  const [regimeTimeline, setRegimeTimeline] = useState<any[]>([])
  const [winRateTrend, setWinRateTrend] = useState<any[]>([])
  const [drawdownHistory, setDrawdownHistory] = useState<any[]>([])
  const [metadata, setMetadata] = useState<any>(null)

  // Filters for journal
  const [jTicker, setJTicker] = useState('')
  const [jStrategy, setJStrategy] = useState('')
  const [jBot, setJBot] = useState('')
  const [jPage, setJPage] = useState(0)
  const PAGE_SIZE = 25

  // Win rate window
  const [wrWindow, setWrWindow] = useState(20)

  // Trade Autopsy — expanded card index
  const [expandedTradeIdx, setExpandedTradeIdx] = useState<number | null>(null)

  const refresh = useCallback(async () => {
    try {
      const results = await Promise.allSettled([
        fetchAPI<any>('/api/history/equity-curve?days=365'),
        fetchAPI<any>(`/api/history/trade-journal?limit=${PAGE_SIZE}&offset=${jPage * PAGE_SIZE}${jTicker ? `&ticker=${jTicker}` : ''}${jStrategy ? `&strategy=${jStrategy}` : ''}${jBot ? `&bot=${jBot}` : ''}`),
        fetchAPI<any>('/api/history/strategy-comparison?days=90'),
        fetchAPI<any>('/api/history/regime-timeline?days=180'),
        fetchAPI<any>(`/api/history/win-rate-trend?window=${wrWindow}&days=180`),
        fetchAPI<any>('/api/history/drawdown-history?days=365'),
        fetchAPI<any>('/api/metadata'),
      ])
      const get = (i: number) => results[i].status === 'fulfilled' ? (results[i] as any).value : null
      if (get(0)) setEquityCurve(get(0).snapshots || [])
      if (get(1)) setJournal(get(1))
      if (get(2)) setStrategies(get(2).comparisons || [])
      if (get(3)) setRegimeTimeline(get(3).timeline || [])
      if (get(4)) setWinRateTrend(get(4).trend || [])
      if (get(5)) setDrawdownHistory(get(5).series || [])
      if (get(6)) setMetadata(get(6))
      setLastUpdate(new Date().toLocaleTimeString())
      setLoading(false)
    } catch {
      setLoading(false)
    }
  }, [jTicker, jStrategy, jBot, jPage, wrWindow])

  useEffect(() => {
    refresh()
    const i = setInterval(refresh, 30000)
    return () => clearInterval(i)
  }, [refresh])

  const tabs: { key: Tab; label: string }[] = [
    { key: 'equity', label: 'Equity Curve' },
    { key: 'journal', label: 'Trade Journal' },
    { key: 'strategies', label: 'Strategy Comparison' },
    { key: 'regime', label: 'Regime Timeline' },
    { key: 'winrate', label: 'Win Rate Trend' },
    { key: 'drawdown', label: 'Drawdown History' },
  ]

  return (
    <div className="min-h-screen p-4 max-w-[1920px] mx-auto">
      {/* Header */}
      <header className="flex items-center justify-between mb-4">
        <div className="flex items-center gap-4">
          <h1 className="text-2xl font-bold tracking-tight">
            NZT-48 <span className="text-nzt-accent">Historic Analysis</span>
          </h1>
          <span className="text-xs text-nzt-muted">Last update: {lastUpdate || '--:--:--'}</span>
        </div>
        <NavTabs active="history" />
      </header>

      {/* Sub-Tabs */}
      <div className="flex gap-1 mb-4 overflow-x-auto">
        {tabs.map(t => (
          <button
            key={t.key}
            onClick={() => setTab(t.key)}
            className={clsx(
              'px-3 py-1.5 text-xs rounded border whitespace-nowrap transition',
              tab === t.key
                ? 'border-nzt-accent/50 text-nzt-accent bg-nzt-accent/10'
                : 'border-nzt-border text-nzt-muted hover:text-nzt-text hover:border-nzt-accent/50'
            )}
          >
            {t.label}
          </button>
        ))}
      </div>

      {loading && (
        <div className="flex items-center justify-center py-20">
          <div className="text-nzt-muted text-sm animate-pulse">Loading historic data...</div>
        </div>
      )}

      {!loading && (
        <>
          {/* Tab: Equity Curve */}
          {tab === 'equity' && (
            <Card title="EQUITY CURVE (365 Days)">
              {equityCurve.length === 0 ? (
                <p className="text-nzt-muted text-sm text-center py-8">No equity data yet</p>
              ) : (
                <>
                  <div className="grid grid-cols-4 gap-3 mb-4">
                    <div className="text-center">
                      <div className="text-xs text-nzt-muted">Starting</div>
                      <div className="text-lg font-bold font-mono">{fmtDollar(equityCurve[0]?.starting_equity || 10000)}</div>
                    </div>
                    <div className="text-center">
                      <div className="text-xs text-nzt-muted">Current</div>
                      <div className={clsx('text-lg font-bold font-mono', (equityCurve[equityCurve.length - 1]?.ending_equity || 0) >= (equityCurve[0]?.starting_equity || 10000) ? 'text-nzt-accent' : 'text-nzt-danger')}>
                        {fmtDollar(equityCurve[equityCurve.length - 1]?.ending_equity || 10000)}
                      </div>
                    </div>
                    <div className="text-center">
                      <div className="text-xs text-nzt-muted">Peak</div>
                      <div className="text-lg font-bold font-mono">{fmtDollar(Math.max(...equityCurve.map(e => e.ending_equity || 0)))}</div>
                    </div>
                    <div className="text-center">
                      <div className="text-xs text-nzt-muted">Return</div>
                      <div className={clsx('text-lg font-bold font-mono',
                        ((equityCurve[equityCurve.length - 1]?.ending_equity || 10000) - (equityCurve[0]?.starting_equity || 10000)) >= 0 ? 'text-nzt-accent' : 'text-nzt-danger'
                      )}>
                        {fmtPct(((equityCurve[equityCurve.length - 1]?.ending_equity || 10000) - (equityCurve[0]?.starting_equity || 10000)) / (equityCurve[0]?.starting_equity || 10000) * 100)}
                      </div>
                    </div>
                  </div>
                  <div className="h-48 flex items-end gap-px">
                    {equityCurve.map((s, i) => {
                      const base = equityCurve[0]?.starting_equity || 10000
                      const eq = s.ending_equity || base
                      const minEq = Math.min(...equityCurve.map(e => e.ending_equity || base))
                      const maxEq = Math.max(...equityCurve.map(e => e.ending_equity || base))
                      const range = maxEq - minEq || 1
                      const height = Math.max(2, ((eq - minEq) / range) * 100)
                      const pct = ((eq - base) / base) * 100
                      return (
                        <div key={i} className="flex-1 flex flex-col justify-end" title={`${s.date}: ${fmtDollar(eq)} (${pct >= 0 ? '+' : ''}${fmt(pct, 1)}%)`}>
                          <div
                            className={clsx('rounded-t-sm min-w-[1px]', pct >= 0 ? 'bg-nzt-accent/70' : 'bg-nzt-danger/70')}
                            style={{ height: `${height}%` }}
                          />
                        </div>
                      )
                    })}
                  </div>
                  <div className="flex justify-between text-[10px] text-nzt-muted mt-1">
                    <span>{equityCurve[0]?.date || ''}</span>
                    <span>{equityCurve[equityCurve.length - 1]?.date || ''}</span>
                  </div>
                </>
              )}
            </Card>
          )}

          {/* Tab: Trade Journal */}
          {tab === 'journal' && (
            <>
            <Card title={`TRADE JOURNAL (${journal.total} trades)`}>
              {/* Filters */}
              <div className="flex gap-2 mb-3">
                <input
                  type="text" placeholder="Ticker..." value={jTicker}
                  onChange={e => { setJTicker(e.target.value.toUpperCase()); setJPage(0) }}
                  className="px-2 py-1 text-xs bg-nzt-bg border border-nzt-border rounded text-nzt-text w-24"
                />
                <input
                  type="text" placeholder="Strategy..." value={jStrategy}
                  onChange={e => { setJStrategy(e.target.value.toUpperCase()); setJPage(0) }}
                  className="px-2 py-1 text-xs bg-nzt-bg border border-nzt-border rounded text-nzt-text w-24"
                />
                <select
                  value={jBot} onChange={e => { setJBot(e.target.value); setJPage(0) }}
                  className="px-2 py-1 text-xs bg-nzt-bg border border-nzt-border rounded text-nzt-text"
                >
                  <option value="">All Bots</option>
                  <option value="A">Bot A (ISA)</option>
                  <option value="B">Bot B (US)</option>
                </select>
              </div>
              {/* CSV Export Button */}
              <div className="flex justify-end mb-2">
                <button
                  onClick={() => {
                    try {
                      if (!journal.trades || journal.trades.length === 0) return
                      const headers = ['Date', 'Ticker', 'Direction', 'Strategy', 'Bot', 'Regime', 'Entry', 'Exit', 'R-Multiple', 'P&L']
                      const rows = journal.trades.map((t: any) => [
                        t.entry_time ? new Date(t.entry_time).toISOString().split('T')[0] : '',
                        t.ticker || '',
                        t.direction || '',
                        t.strategy || '',
                        t.bot || '',
                        t.regime || '',
                        t.entry_price ?? '',
                        t.exit_price ?? '',
                        t.r_multiple ?? '',
                        t.net_pnl ?? '',
                      ])
                      const csvContent = [headers, ...rows].map(r => r.map((c: any) => `"${String(c).replace(/"/g, '""')}"`).join(',')).join('\n')
                      const blob = new Blob([csvContent], { type: 'text/csv;charset=utf-8;' })
                      const url = URL.createObjectURL(blob)
                      const link = document.createElement('a')
                      link.href = url
                      link.download = `nzt48_trades_${new Date().toISOString().split('T')[0]}.csv`
                      link.click()
                      URL.revokeObjectURL(url)
                    } catch { /* silent fail */ }
                  }}
                  className="px-3 py-1.5 text-xs rounded border border-nzt-accent/50 text-nzt-accent bg-nzt-accent/10 hover:bg-nzt-accent/20 transition flex items-center gap-1.5"
                  title="Export current trades to CSV file"
                >
                  <svg className="w-3 h-3" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                    <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M12 10v6m0 0l-3-3m3 3l3-3m2 8H7a2 2 0 01-2-2V5a2 2 0 012-2h5.586a1 1 0 01.707.293l5.414 5.414a1 1 0 01.293.707V19a2 2 0 01-2 2z" />
                  </svg>
                  Export CSV
                </button>
              </div>
              {/* Table */}
              <div className="overflow-x-auto">
                <table className="w-full text-xs">
                  <thead>
                    <tr className="border-b border-nzt-border text-nzt-muted">
                      <th className="py-1.5 px-2 text-left">Date</th>
                      <th className="py-1.5 px-2 text-left">Ticker</th>
                      <th className="py-1.5 px-2 text-left">Dir</th>
                      <th className="py-1.5 px-2 text-left">Strategy</th>
                      <th className="py-1.5 px-2 text-left">Bot</th>
                      <th className="py-1.5 px-2 text-left">Regime</th>
                      <th className="py-1.5 px-2 text-right">Entry</th>
                      <th className="py-1.5 px-2 text-right">Exit</th>
                      <th className="py-1.5 px-2 text-right">R</th>
                      <th className="py-1.5 px-2 text-right">P&L</th>
                    </tr>
                  </thead>
                  <tbody>
                    {journal.trades.map((t, i) => (
                      <tr key={i} className="border-b border-nzt-border/30 hover:bg-nzt-bg/50">
                        <td className="py-1 px-2 text-nzt-muted">{fmtDate(t.entry_time)}</td>
                        <td className="py-1 px-2"><TickerLink ticker={t.ticker} className="text-xs" /></td>
                        <td className="py-1 px-2"><DirBadge dir={t.direction} /></td>
                        <td className="py-1 px-2">{t.strategy}</td>
                        <td className="py-1 px-2"><BotBadge bot={t.bot || 'B'} /></td>
                        <td className={clsx('py-1 px-2', getRegimeColor(t.regime))}>{t.regime || '--'}</td>
                        <td className="py-1 px-2 text-right font-mono">${fmt(t.entry_price)}</td>
                        <td className="py-1 px-2 text-right font-mono">${fmt(t.exit_price)}</td>
                        <td className={clsx('py-1 px-2 text-right font-mono font-bold', (t.r_multiple || 0) >= 0 ? 'text-nzt-accent' : 'text-nzt-danger')}>
                          {(t.r_multiple || 0) >= 0 ? '+' : ''}{fmt(t.r_multiple)}R
                        </td>
                        <td className={clsx('py-1 px-2 text-right font-mono font-bold', (t.net_pnl || 0) >= 0 ? 'text-nzt-accent' : 'text-nzt-danger')}>
                          {fmtDollar(t.net_pnl)}
                        </td>
                      </tr>
                    ))}
                    {journal.trades.length === 0 && (
                      <tr><td colSpan={10} className="text-center py-6 text-nzt-muted">No trades found</td></tr>
                    )}
                  </tbody>
                </table>
              </div>
              {/* Pagination */}
              <div className="flex justify-between items-center mt-3">
                <span className="text-xs text-nzt-muted">
                  Showing {jPage * PAGE_SIZE + 1}-{Math.min((jPage + 1) * PAGE_SIZE, journal.total)} of {journal.total}
                </span>
                <div className="flex gap-1">
                  <button
                    onClick={() => setJPage(Math.max(0, jPage - 1))}
                    disabled={jPage === 0}
                    className="px-2 py-1 text-xs rounded border border-nzt-border text-nzt-muted disabled:opacity-30"
                  >Prev</button>
                  <button
                    onClick={() => setJPage(jPage + 1)}
                    disabled={(jPage + 1) * PAGE_SIZE >= journal.total}
                    className="px-2 py-1 text-xs rounded border border-nzt-border text-nzt-muted disabled:opacity-30"
                  >Next</button>
                </div>
              </div>
            </Card>

              {/* Trade Autopsy Cards */}
              {journal.trades && journal.trades.length > 0 && (
                <div className="mt-3">
                  <div className="flex items-center gap-2 mb-2">
                    <h3 className="text-xs font-bold text-nzt-accent uppercase tracking-wider">Trade Autopsy</h3>
                    <span className="text-[10px] text-nzt-muted">Click a trade to expand analysis</span>
                  </div>
                  <div className="space-y-1.5">
                    {journal.trades.map((t: any, i: number) => {
                      const isExpanded = expandedTradeIdx === i
                      const rMultiple = t.r_multiple ?? 0
                      const isWin = rMultiple >= 0
                      const entryConditions: string[] = []
                      if (t.strategy) entryConditions.push(`Strategy: ${t.strategy}`)
                      if (t.regime) entryConditions.push(`Regime: ${t.regime}`)
                      if (t.direction) entryConditions.push(`Direction: ${t.direction}`)
                      if (t.confidence) entryConditions.push(`Confidence: ${t.confidence}%`)

                      return (
                        <div key={i} className={clsx(
                          'rounded border transition-all',
                          isExpanded ? 'border-nzt-accent/50 bg-nzt-card' : 'border-nzt-border bg-nzt-bg hover:border-nzt-border/80'
                        )}>
                          {/* Collapsed header */}
                          <button
                            onClick={() => setExpandedTradeIdx(isExpanded ? null : i)}
                            className="w-full flex items-center justify-between p-2 text-xs text-left"
                          >
                            <div className="flex items-center gap-2">
                              <span className={clsx(
                                'w-1.5 h-1.5 rounded-full',
                                isWin ? 'bg-nzt-accent' : 'bg-nzt-danger'
                              )} />
                              <span className="text-nzt-muted">{fmtDate(t.entry_time)}</span>
                              <DirBadge dir={t.direction} />
                              <TickerLink ticker={t.ticker} className="text-xs" />
                              <span className="text-nzt-muted">{t.strategy}</span>
                            </div>
                            <div className="flex items-center gap-3">
                              <span className={clsx('font-mono font-bold', isWin ? 'text-nzt-accent' : 'text-nzt-danger')}>
                                {rMultiple >= 0 ? '+' : ''}{fmt(rMultiple)}R
                              </span>
                              <span className={clsx('font-mono', (t.net_pnl || 0) >= 0 ? 'text-nzt-accent' : 'text-nzt-danger')}>
                                {fmtDollar(t.net_pnl)}
                              </span>
                              <span className="text-nzt-muted">{isExpanded ? '▲' : '▼'}</span>
                            </div>
                          </button>

                          {/* Expanded autopsy */}
                          {isExpanded && (
                            <div className="px-3 pb-3 border-t border-nzt-border/30 space-y-2">
                              <div className="grid grid-cols-3 gap-3 mt-2">
                                {/* Entry Conditions */}
                                <div className="bg-nzt-bg rounded p-2">
                                  <div className="text-[10px] font-bold text-nzt-accent uppercase mb-1">Entry Conditions</div>
                                  <div className="space-y-0.5">
                                    {entryConditions.map((c, ci) => (
                                      <div key={ci} className="text-[10px] text-nzt-text">{c}</div>
                                    ))}
                                    <div className="text-[10px] text-nzt-muted">Entry: ${fmt(t.entry_price)}</div>
                                    {t.initial_stop && <div className="text-[10px] text-red-400">Stop: ${fmt(t.initial_stop)}</div>}
                                    {t.initial_target && <div className="text-[10px] text-green-400">Target: ${fmt(t.initial_target)}</div>}
                                  </div>
                                </div>

                                {/* Exit Analysis */}
                                <div className="bg-nzt-bg rounded p-2">
                                  <div className="text-[10px] font-bold text-nzt-accent uppercase mb-1">Exit Analysis</div>
                                  <div className="space-y-0.5">
                                    <div className="text-[10px] text-nzt-text">Exit: ${fmt(t.exit_price)}</div>
                                    <div className="text-[10px] text-nzt-text">Reason: <span className="text-nzt-accent">{t.exit_reason || t.close_reason || 'N/A'}</span></div>
                                    {t.hold_duration && <div className="text-[10px] text-nzt-muted">Duration: {t.hold_duration}</div>}
                                    {t.peak_r != null && <div className="text-[10px] text-green-400">Peak: +{fmt(t.peak_r)}R</div>}
                                    {t.trough_r != null && <div className="text-[10px] text-red-400">Trough: {fmt(t.trough_r)}R</div>}
                                  </div>
                                </div>

                                {/* Assessment */}
                                <div className="bg-nzt-bg rounded p-2">
                                  <div className="text-[10px] font-bold text-nzt-accent uppercase mb-1">Assessment</div>
                                  <div className="space-y-0.5">
                                    <div className="flex items-center gap-1">
                                      <span className="text-[10px] text-nzt-muted">R-Multiple:</span>
                                      <span className={clsx('text-xs font-mono font-bold', isWin ? 'text-nzt-accent' : 'text-nzt-danger')}>
                                        {rMultiple >= 0 ? '+' : ''}{fmt(rMultiple)}R
                                      </span>
                                    </div>
                                    <div className="text-[10px] text-nzt-muted">
                                      {isWin
                                        ? rMultiple >= 2 ? 'Excellent execution. Target hit or exceeded.' :
                                          rMultiple >= 1 ? 'Good trade. Risk well managed.' :
                                          'Marginal win. Consider tighter management.'
                                        : rMultiple >= -0.5 ? 'Small loss. Risk well controlled.' :
                                          rMultiple >= -1 ? 'Full stop hit. Normal loss.' :
                                          'Slippage or gap risk. Review sizing.'
                                      }
                                    </div>
                                    {t.peak_r != null && rMultiple < (t.peak_r || 0) * 0.5 && (
                                      <div className="text-[10px] text-amber-400">
                                        Gave back {fmt((t.peak_r || 0) - rMultiple)}R from peak
                                      </div>
                                    )}
                                  </div>
                                </div>
                              </div>
                            </div>
                          )}
                        </div>
                      )
                    })}
                  </div>
                </div>
              )}
            </>
          )}

          {/* Tab: Strategy Comparison */}
          {tab === 'strategies' && (
            <Card title="STRATEGY COMPARISON (90 Days — Weekly P&L)">
              {strategies.length === 0 ? (
                <p className="text-nzt-muted text-sm text-center py-8">No strategy data yet</p>
              ) : (
                <div className="overflow-x-auto">
                  <table className="w-full text-xs">
                    <thead>
                      <tr className="border-b border-nzt-border text-nzt-muted">
                        <th className="py-1.5 px-2 text-left">Week</th>
                        <th className="py-1.5 px-2 text-left">Strategy</th>
                        <th className="py-1.5 px-2 text-right">Trades</th>
                        <th className="py-1.5 px-2 text-right">Win Rate</th>
                        <th className="py-1.5 px-2 text-right">Avg R</th>
                        <th className="py-1.5 px-2 text-right">P&L</th>
                      </tr>
                    </thead>
                    <tbody>
                      {strategies.map((s, i) => (
                        <tr key={i} className="border-b border-nzt-border/30 hover:bg-nzt-bg/50">
                          <td className="py-1 px-2 text-nzt-muted">{s.week}</td>
                          <td className="py-1 px-2 font-bold">{s.strategy}</td>
                          <td className="py-1 px-2 text-right">{s.trades}</td>
                          <td className={clsx('py-1 px-2 text-right font-mono', (s.win_rate || 0) >= 50 ? 'text-nzt-accent' : 'text-nzt-danger')}>
                            {fmtPct(s.win_rate)}
                          </td>
                          <td className={clsx('py-1 px-2 text-right font-mono', (s.avg_r || 0) >= 0 ? 'text-nzt-accent' : 'text-nzt-danger')}>
                            {fmt(s.avg_r)}R
                          </td>
                          <td className={clsx('py-1 px-2 text-right font-mono font-bold', (s.total_pnl || 0) >= 0 ? 'text-nzt-accent' : 'text-nzt-danger')}>
                            {fmtDollar(s.total_pnl)}
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              )}
            </Card>
          )}

          {/* Tab: Regime Timeline */}
          {tab === 'regime' && (
            <Card title="REGIME TIMELINE (180 Days)">
              {regimeTimeline.length === 0 ? (
                <p className="text-nzt-muted text-sm text-center py-8">No regime data yet</p>
              ) : (
                <>
                  {/* Visual timeline bar */}
                  <div className="h-12 flex gap-px mb-4 rounded overflow-hidden">
                    {regimeTimeline.map((r, i) => {
                      const regime = r.regime || r.state || 'UNKNOWN'
                      const bgColor = regime.includes('UP_STRONG') ? 'bg-green-500'
                        : regime.includes('UP_MOD') ? 'bg-green-400'
                        : regime.includes('DOWN_STRONG') ? 'bg-red-500'
                        : regime.includes('DOWN_MOD') ? 'bg-red-400'
                        : regime.includes('RANGE') ? 'bg-yellow-500'
                        : regime.includes('HIGH_VOL') ? 'bg-orange-500'
                        : regime.includes('RISK_OFF') ? 'bg-red-600'
                        : regime.includes('SHOCK') ? 'bg-red-700'
                        : 'bg-gray-600'
                      return (
                        <div
                          key={i}
                          className={clsx('flex-1 min-w-[2px]', bgColor)}
                          title={`${r.timestamp || ''}: ${regime}`}
                        />
                      )
                    })}
                  </div>
                  {/* Legend */}
                  <div className="flex flex-wrap gap-3 mb-4 text-[10px]">
                    {(metadata?.regime_display
                      ? Object.values(metadata.regime_display).map((r: any) => ({ label: r.label, color: r.bg }))
                      : [
                        { label: 'UP STRONG', color: 'bg-green-500' },
                        { label: 'UP MOD', color: 'bg-green-400' },
                        { label: 'RANGE', color: 'bg-yellow-500' },
                        { label: 'DOWN MOD', color: 'bg-red-400' },
                        { label: 'DOWN STRONG', color: 'bg-red-500' },
                        { label: 'HIGH VOL', color: 'bg-orange-500' },
                        { label: 'RISK OFF', color: 'bg-red-600' },
                        { label: 'SHOCK', color: 'bg-red-700' },
                      ]
                    ).map((l: any) => (
                      <div key={l.label} className="flex items-center gap-1">
                        <div className={clsx('w-3 h-3 rounded', l.color)} />
                        <span className="text-nzt-muted">{l.label}</span>
                      </div>
                    ))}
                  </div>
                  {/* Table */}
                  <div className="overflow-x-auto max-h-96">
                    <table className="w-full text-xs">
                      <thead>
                        <tr className="border-b border-nzt-border text-nzt-muted">
                          <th className="py-1.5 px-2 text-left">Timestamp</th>
                          <th className="py-1.5 px-2 text-left">Regime</th>
                          <th className="py-1.5 px-2 text-right">VIX</th>
                          <th className="py-1.5 px-2 text-right">Duration (bars)</th>
                        </tr>
                      </thead>
                      <tbody>
                        {regimeTimeline.slice().reverse().slice(0, 50).map((r, i) => (
                          <tr key={i} className="border-b border-nzt-border/30">
                            <td className="py-1 px-2 text-nzt-muted">{fmtDate(r.timestamp)} {fmtTime(r.timestamp)}</td>
                            <td className={clsx('py-1 px-2 font-bold', getRegimeColor(r.regime || r.state))}>{r.regime || r.state || '--'}</td>
                            <td className="py-1 px-2 text-right font-mono">{fmt(r.vix, 1)}</td>
                            <td className="py-1 px-2 text-right font-mono">{r.duration_bars || '--'}</td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                </>
              )}
            </Card>
          )}

          {/* Tab: Win Rate Trend */}
          {tab === 'winrate' && (
            <Card title={`WIN RATE TREND (Rolling ${wrWindow}-Trade Window)`}>
              <div className="flex gap-2 mb-3">
                {[10, 20, 40, 60].map(w => (
                  <button
                    key={w}
                    onClick={() => setWrWindow(w)}
                    className={clsx(
                      'px-2 py-1 text-xs rounded border',
                      wrWindow === w ? 'border-nzt-accent text-nzt-accent bg-nzt-accent/10' : 'border-nzt-border text-nzt-muted'
                    )}
                  >
                    {w} trades
                  </button>
                ))}
              </div>
              {winRateTrend.length === 0 ? (
                <p className="text-nzt-muted text-sm text-center py-8">Not enough trades for trend analysis</p>
              ) : (
                <>
                  <div className="h-40 flex items-end gap-px relative">
                    {/* 50% line */}
                    <div className="absolute left-0 right-0 border-t border-dashed border-nzt-muted/30" style={{ bottom: '50%' }} />
                    <div className="absolute right-0 text-[10px] text-nzt-muted" style={{ bottom: '50%' }}>50%</div>
                    {winRateTrend.map((p, i) => {
                      const height = Math.max(2, p.win_rate)
                      return (
                        <div key={i} className="flex-1 flex flex-col justify-end" title={`Trade #${p.trade_index}: ${p.win_rate}% WR`}>
                          <div
                            className={clsx('rounded-t-sm min-w-[1px]', p.win_rate >= 50 ? 'bg-nzt-accent/70' : 'bg-nzt-danger/70')}
                            style={{ height: `${height}%` }}
                          />
                        </div>
                      )
                    })}
                  </div>
                  <div className="flex justify-between text-[10px] text-nzt-muted mt-1">
                    <span>Trade #{winRateTrend[0]?.trade_index}</span>
                    <span>Current: {winRateTrend[winRateTrend.length - 1]?.win_rate}%</span>
                    <span>Trade #{winRateTrend[winRateTrend.length - 1]?.trade_index}</span>
                  </div>
                </>
              )}
            </Card>
          )}

          {/* Tab: Drawdown History */}
          {tab === 'drawdown' && (
            <Card title="DRAWDOWN HISTORY (365 Days)">
              {drawdownHistory.length === 0 ? (
                <p className="text-nzt-muted text-sm text-center py-8">No drawdown data yet</p>
              ) : (
                <>
                  {/* Summary */}
                  <div className="grid grid-cols-3 gap-3 mb-4">
                    <div className="text-center">
                      <div className="text-xs text-nzt-muted">Max Drawdown</div>
                      <div className="text-lg font-bold text-nzt-danger font-mono">
                        -{fmt(Math.max(...drawdownHistory.map(d => d.drawdown_pct)), 2)}%
                      </div>
                    </div>
                    <div className="text-center">
                      <div className="text-xs text-nzt-muted">Current</div>
                      <div className={clsx('text-lg font-bold font-mono', getDDColor(drawdownHistory[drawdownHistory.length - 1]?.level))}>
                        -{fmt(drawdownHistory[drawdownHistory.length - 1]?.drawdown_pct, 2)}%
                      </div>
                    </div>
                    <div className="text-center">
                      <div className="text-xs text-nzt-muted">Status</div>
                      <div className={clsx('text-lg font-bold', getDDColor(drawdownHistory[drawdownHistory.length - 1]?.level))}>
                        {drawdownHistory[drawdownHistory.length - 1]?.level || 'GREEN'}
                      </div>
                    </div>
                  </div>
                  {/* Inverted chart (drawdown goes down) */}
                  <div className="h-40 flex items-start gap-px">
                    {drawdownHistory.map((d, i) => {
                      const maxDD = Math.max(...drawdownHistory.map(x => x.drawdown_pct)) || 1
                      const height = Math.max(1, (d.drawdown_pct / maxDD) * 100)
                      const color = d.level === 'GREEN' ? 'bg-nzt-accent/50'
                        : d.level === 'YELLOW' ? 'bg-yellow-500/50'
                        : d.level === 'ORANGE' ? 'bg-orange-500/50'
                        : d.level === 'RED' ? 'bg-red-500/50'
                        : d.level === 'CRITICAL' ? 'bg-red-600/50'
                        : 'bg-red-700/50'
                      return (
                        <div key={i} className="flex-1 flex flex-col items-stretch" title={`${d.date}: -${fmt(d.drawdown_pct)}% (${d.level})`}>
                          <div className={clsx('rounded-b-sm min-w-[1px]', color)} style={{ height: `${height}%` }} />
                        </div>
                      )
                    })}
                  </div>
                  <div className="flex justify-between text-[10px] text-nzt-muted mt-1">
                    <span>{drawdownHistory[0]?.date || ''}</span>
                    <span>{drawdownHistory[drawdownHistory.length - 1]?.date || ''}</span>
                  </div>
                  {/* Recovery level bands */}
                  <div className="flex gap-2 mt-3 text-[10px]">
                    {(metadata?.drawdown_levels ?? [
                      { label: 'GREEN <3%', color: 'bg-nzt-accent/50' },
                      { label: 'YELLOW 3-5%', color: 'bg-yellow-500/50' },
                      { label: 'ORANGE 5-8%', color: 'bg-orange-500/50' },
                      { label: 'RED 8-10%', color: 'bg-red-500/50' },
                      { label: 'CRITICAL 10-12%', color: 'bg-red-600/50' },
                      { label: 'EMERGENCY 12%+', color: 'bg-red-700/50' },
                    ]).map((b: any) => (
                      <div key={b.label} className="flex items-center gap-1">
                        <div className={clsx('w-3 h-3 rounded', b.color)} />
                        <span className="text-nzt-muted">{b.label}</span>
                      </div>
                    ))}
                  </div>
                </>
              )}
            </Card>
          )}
        </>
      )}
    </div>
  )
}
