'use client'

import { useState, useEffect, useCallback, useMemo } from 'react'
import clsx from 'clsx'
import Link from 'next/link'
import { Card, DirBadge, StatusBadge, TickerLink, NavTabs, LaneBadge, ScoreBar, SectorSignalBadge } from '../lib/components'
import { getAPI, fetchAPI, fmt, fmtDollar, fmtPct } from '../lib/api'
import { getRegimeColor, getVixColor, getVixLabel, getHeatmapBg, getRiskBudgetColor, getLaneBorder, getLaneBg, getScoreColor } from '../lib/colors'

// === Types ===

interface MarketData {
  spy_price: number
  spy_change: number
  spy_change_pct: number
  vix: number
  regime: string | null
  regime_duration: number
}

interface TickerData {
  ticker: string
  price: number
  change_pct: number
  volume: number
  rvol: number
  has_position: boolean
  direction: string | null
}

interface ScanData {
  signals_generated_24h: number
  trades_taken_24h: number
  last_scan_time: string | null
  strategies_firing: string[]
}

interface RiskData {
  current_equity: number
  peak_equity: number
  drawdown_pct: number
  daily_pnl: number
  risk_budget_used: number
  open_risk_dollars: number
}

interface StrategyData {
  strategy: string
  trades: number
  win_rate: number
  total_pnl: number
  avg_r: number
  status: string
}

type SortKey = keyof StrategyData
type SortDir = 'asc' | 'desc'

// === ISA Fund type ===
interface ISAFund {
  ticker: string
  label: string
  type: string
  category?: string
  status?: string
  provider?: string
}

// === Main Component ===
export default function AnalysisPage() {
  const [market, setMarket] = useState<MarketData | null>(null)
  const [tickers, setTickers] = useState<TickerData[]>([])
  const [scans, setScans] = useState<ScanData | null>(null)
  const [risk, setRisk] = useState<RiskData | null>(null)
  const [strategies, setStrategies] = useState<StrategyData[]>([])
  const [loading, setLoading] = useState(true)
  const [lastUpdate, setLastUpdate] = useState<string>('')
  const [sortKey, setSortKey] = useState<SortKey>('total_pnl')
  const [sortDir, setSortDir] = useState<SortDir>('desc')
  const [heatmapData, setHeatmapData] = useState<any[]>([])
  const [isaUniverse, setIsaUniverse] = useState<any>(null)
  const [equitySnapshots, setEquitySnapshots] = useState<any>(null)
  const [isaFunds, setIsaFunds] = useState<ISAFund[]>([])
  const [usTickers, setUsTickers] = useState<string[]>([])

  // Expandable ticker state
  const [expandedTicker, setExpandedTicker] = useState<string | null>(null)
  const [tickerDetails, setTickerDetails] = useState<Record<string, any>>({})
  const [loadingTicker, setLoadingTicker] = useState<string | null>(null)

  // Lane assignments + Sector rotation
  const [laneAssignments, setLaneAssignments] = useState<any>(null)
  const [sectorRotation, setSectorRotation] = useState<any>(null)
  const [regimeMatrix, setRegimeMatrix] = useState<any>(null)

  // Tab: US Equities vs ISA Funds vs Lane View
  const [universeTab, setUniverseTab] = useState<'lanes' | 'isa' | 'us'>('lanes')

  const refresh = useCallback(async () => {
    try {
      const results = await Promise.allSettled([
        fetchAPI<any>('/api/analysis/market'),
        fetchAPI<any>('/api/analysis/tickers'),
        fetchAPI<any>('/api/analysis/scans'),
        fetchAPI<any>('/api/analysis/risk'),
        fetchAPI<any>('/api/analysis/strategies'),
      ])

      if (results[0].status === 'fulfilled') setMarket(results[0].value)
      if (results[1].status === 'fulfilled') {
        const val = results[1].value
        setTickers(Array.isArray(val) ? val : val?.tickers ?? [])
      }
      if (results[2].status === 'fulfilled') setScans(results[2].value)
      if (results[3].status === 'fulfilled') setRisk(results[3].value)
      if (results[4].status === 'fulfilled') {
        const val = results[4].value
        setStrategies(Array.isArray(val) ? val : val?.strategies ?? [])
      }

      // Extended data + metadata (single source of truth)
      const extResults = await Promise.allSettled([
        fetchAPI<any>('/api/analysis/heatmap'),
        fetchAPI<any>('/api/isa-universe'),
        fetchAPI<any>('/api/equity-snapshots?days=90'),
        fetchAPI<any>('/api/metadata'),
        fetchAPI<any>('/api/lane-assignments').catch(() => null),
        fetchAPI<any>('/api/sector-rotation').catch(() => null),
        fetchAPI<any>('/api/analysis/regime-matrix').catch(() => null),
      ])
      if (extResults[0].status === 'fulfilled') setHeatmapData(extResults[0].value?.heatmap ?? [])
      if (extResults[1].status === 'fulfilled') setIsaUniverse(extResults[1].value)
      if (extResults[2].status === 'fulfilled') setEquitySnapshots(extResults[2].value)
      if (extResults[3].status === 'fulfilled') {
        const meta = extResults[3].value
        if (meta?.isa_tickers?.length) setIsaFunds(meta.isa_tickers)
        if (meta?.us_tickers?.length) setUsTickers(meta.us_tickers)
      }
      if (extResults[4].status === 'fulfilled' && extResults[4].value) setLaneAssignments(extResults[4].value)
      if (extResults[5].status === 'fulfilled' && extResults[5].value) setSectorRotation(extResults[5].value)
      if (extResults[6].status === 'fulfilled' && extResults[6].value) setRegimeMatrix(extResults[6].value)

      setLastUpdate(new Date().toLocaleTimeString())
      setLoading(false)
    } catch {
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    refresh()
    const interval = setInterval(refresh, 60000)
    return () => clearInterval(interval)
  }, [refresh])

  // Fetch institutional data for a ticker
  const fetchTickerData = useCallback(async (sym: string, showLoading = true) => {
    if (showLoading) setLoadingTicker(sym)
    try {
      const data = await fetchAPI<any>(`/api/ticker/${encodeURIComponent(sym)}/institutional`)
      setTickerDetails(prev => ({ ...prev, [sym]: data }))
    } catch {
      setTickerDetails(prev => ({ ...prev, [sym]: { error: true } }))
    }
    if (showLoading) setLoadingTicker(null)
  }, [])

  // Handle ticker click — expand/collapse
  const handleTickerClick = useCallback(async (sym: string) => {
    if (expandedTicker === sym) {
      setExpandedTicker(null)
      return
    }
    setExpandedTicker(sym)
    if (!tickerDetails[sym]) {
      await fetchTickerData(sym, true)
    }
  }, [expandedTicker, tickerDetails, fetchTickerData])

  // Auto-refresh expanded ticker data every 60s
  useEffect(() => {
    if (!expandedTicker) return
    const interval = setInterval(() => {
      fetchTickerData(expandedTicker, false)
    }, 60000)
    return () => clearInterval(interval)
  }, [expandedTicker, fetchTickerData])

  // Sort strategies
  const sortedStrategies = useMemo(() => {
    const sorted = [...strategies]
    sorted.sort((a, b) => {
      const aVal = a[sortKey]
      const bVal = b[sortKey]
      if (typeof aVal === 'number' && typeof bVal === 'number') {
        return sortDir === 'asc' ? aVal - bVal : bVal - aVal
      }
      return sortDir === 'asc'
        ? String(aVal).localeCompare(String(bVal))
        : String(bVal).localeCompare(String(aVal))
    })
    return sorted
  }, [strategies, sortKey, sortDir])

  const handleSort = (key: SortKey) => {
    if (sortKey === key) {
      setSortDir(sortDir === 'asc' ? 'desc' : 'asc')
    } else {
      setSortKey(key)
      setSortDir('desc')
    }
  }

  // Build ticker grid from API data or metadata-driven placeholders
  const tickerGrid = useMemo(() => {
    if (tickers.length > 0) return tickers
    if (usTickers.length > 0) {
      return usTickers.map((t: string) => ({
        ticker: t, price: 0, change_pct: 0, volume: 0, rvol: 0,
        has_position: false, direction: null,
      }))
    }
    return []
  }, [tickers, usTickers])

  return (
    <div className="min-h-screen p-4 max-w-[1920px] mx-auto">
      {/* Header with Navigation */}
      <header className="flex items-center justify-between mb-6">
        <div className="flex items-center gap-4">
          <h1 className="text-2xl font-bold tracking-tight">
            NZT-48 <span className="text-nzt-accent">Data Analysis</span>
          </h1>
          <span className="text-xs text-nzt-muted">
            Last update: {lastUpdate || '--:--:--'}
          </span>
        </div>
        <NavTabs active="analysis" />
      </header>

      {/* Loading State */}
      {loading && (
        <div className="flex items-center justify-center py-20">
          <div className="text-nzt-muted text-sm animate-pulse">
            Connecting to NZT-48 engine...
          </div>
        </div>
      )}

      {!loading && (
        <>
          {/* Section 1: Market Overview Row */}
          <div className="grid grid-cols-4 gap-4 mb-4">
            <Card title="SPY PRICE">
              <div className="text-center">
                <div className="text-3xl font-bold font-mono">
                  ${market?.spy_price?.toFixed(2) ?? '0.00'}
                </div>
                <div className={clsx(
                  'text-sm font-mono mt-1',
                  (market?.spy_change ?? 0) >= 0 ? 'text-nzt-accent' : 'text-nzt-danger'
                )}>
                  {(market?.spy_change ?? 0) >= 0 ? '+' : ''}
                  {market?.spy_change?.toFixed(2) ?? '0.00'}{' '}
                  ({(market?.spy_change_pct ?? 0) >= 0 ? '+' : ''}
                  {market?.spy_change_pct?.toFixed(2) ?? '0.00'}%)
                </div>
              </div>
            </Card>

            <Card title="VIX LEVEL">
              <div className="text-center">
                <div className={clsx('text-3xl font-bold font-mono', getVixColor(market?.vix ?? 0))}>
                  {market?.vix?.toFixed(2) ?? '0.00'}
                </div>
                <div className="text-xs text-nzt-muted mt-1">
                  {getVixLabel(market?.vix ?? 0)}
                </div>
              </div>
            </Card>

            <Card title="CURRENT REGIME">
              <div className="text-center">
                <div className={clsx('text-2xl font-bold', getRegimeColor(market?.regime ?? undefined))}>
                  {market?.regime || 'UNKNOWN'}
                </div>
                <div className="text-xs text-nzt-muted mt-1">
                  Duration: {market?.regime_duration ?? 0} bars
                </div>
              </div>
            </Card>

            <Card title="SYSTEM STATUS">
              <div className="text-center space-y-1">
                <div className="flex justify-between text-sm">
                  <span className="text-nzt-muted">Signals (24h)</span>
                  <span className="font-bold text-nzt-accent">{scans?.signals_generated_24h ?? 0}</span>
                </div>
                <div className="flex justify-between text-sm">
                  <span className="text-nzt-muted">Trades (24h)</span>
                  <span className="font-bold">{scans?.trades_taken_24h ?? 0}</span>
                </div>
                <div className="flex justify-between text-sm">
                  <span className="text-nzt-muted">Last Scan</span>
                  <span className="text-xs font-mono">
                    {scans?.last_scan_time
                      ? new Date(scans.last_scan_time).toLocaleTimeString()
                      : '--:--'}
                  </span>
                </div>
              </div>
            </Card>
          </div>

          {/* Section 2: EXPANDABLE TICKER & FUND UNIVERSE */}
          <div className="mb-4">
            {/* Tab switcher */}
            <div className="flex items-center gap-2 mb-3">
              <h2 className="text-sm font-bold text-nzt-muted uppercase tracking-wider mr-3">Universe</h2>
              <button
                onClick={() => setUniverseTab('lanes')}
                className={clsx(
                  'px-4 py-1.5 rounded text-sm font-bold transition',
                  universeTab === 'lanes'
                    ? 'bg-green-900/50 text-green-300 border border-green-500/50'
                    : 'bg-nzt-card text-nzt-muted border border-nzt-border hover:text-nzt-text'
                )}
              >
                Lane View ({laneAssignments?.assignments?.length || 0})
              </button>
              <button
                onClick={() => setUniverseTab('isa')}
                className={clsx(
                  'px-4 py-1.5 rounded text-sm font-bold transition',
                  universeTab === 'isa'
                    ? 'bg-blue-900/50 text-blue-300 border border-blue-500/50'
                    : 'bg-nzt-card text-nzt-muted border border-nzt-border hover:text-nzt-text'
                )}
              >
                ISA Funds ({isaFunds.length})
              </button>
              <button
                onClick={() => setUniverseTab('us')}
                className={clsx(
                  'px-4 py-1.5 rounded text-sm font-bold transition',
                  universeTab === 'us'
                    ? 'bg-purple-900/50 text-purple-300 border border-purple-500/50'
                    : 'bg-nzt-card text-nzt-muted border border-nzt-border hover:text-nzt-text'
                )}
              >
                US Equities ({tickerGrid.length})
              </button>
            </div>

            {/* Lane View — grouped by TRADE/WATCH/INTEL/ABSTAIN */}
            {universeTab === 'lanes' && laneAssignments?.assignments && (
              <div className="space-y-4">
                {['TRADE', 'WATCH', 'INTEL', 'ABSTAIN'].map(lane => {
                  const items = laneAssignments.assignments.filter((a: any) => a.lane === lane)
                  if (items.length === 0) return null
                  return (
                    <div key={lane} className={clsx('rounded-lg border p-3', getLaneBorder(lane), getLaneBg(lane))}>
                      <div className="flex items-center gap-3 mb-3">
                        <LaneBadge lane={lane} />
                        <span className="text-sm font-bold text-nzt-text">{items.length} instruments</span>
                        {lane === 'TRADE' && <span className="text-[10px] text-green-400">Full entry/stop/target available</span>}
                        {lane === 'WATCH' && <span className="text-[10px] text-amber-400">Monitor for upgrade to TRADE</span>}
                      </div>
                      <div className="overflow-x-auto">
                        <table className="w-full text-xs">
                          <thead>
                            <tr className="border-b border-nzt-border/30 text-nzt-muted">
                              <th className="py-1 px-2 text-left">Ticker</th>
                              <th className="py-1 px-2 text-center">Liq</th>
                              <th className="py-1 px-2 text-right">Score</th>
                              {(lane === 'TRADE' || lane === 'WATCH') && (
                                <>
                                  <th className="py-1 px-2 text-right">Entry</th>
                                  <th className="py-1 px-2 text-right">Stop</th>
                                  <th className="py-1 px-2 text-right">Target</th>
                                  <th className="py-1 px-2 text-right">R:R</th>
                                </>
                              )}
                              <th className="py-1 px-2 text-left">Reason</th>
                            </tr>
                          </thead>
                          <tbody>
                            {items.map((a: any, i: number) => (
                              <tr key={i} className="border-b border-nzt-border/20 hover:bg-nzt-bg/50">
                                <td className="py-1.5 px-2 font-bold">
                                  <TickerLink ticker={a.ticker} className="text-xs" />
                                </td>
                                <td className="py-1.5 px-2 text-center">
                                  {(() => {
                                    try {
                                      const vol = a.volume || a.avg_volume || 0
                                      const tier = vol >= 10_000_000 ? 1 : vol >= 2_000_000 ? 2 : vol >= 500_000 ? 3 : 4
                                      const colors: Record<number, string> = {
                                        1: 'bg-green-900/40 text-green-400',
                                        2: 'bg-yellow-900/40 text-yellow-400',
                                        3: 'bg-orange-900/40 text-orange-400',
                                        4: 'bg-red-900/40 text-red-400',
                                      }
                                      return (
                                        <span className={clsx('text-[9px] font-bold px-1 py-0.5 rounded', colors[tier])}>
                                          T{tier}
                                        </span>
                                      )
                                    } catch { return <span className="text-[9px] text-nzt-muted">--</span> }
                                  })()}
                                </td>
                                <td className={clsx('py-1.5 px-2 text-right font-mono font-bold', getScoreColor(a.score || 0))}>
                                  {a.score || 0}
                                </td>
                                {(lane === 'TRADE' || lane === 'WATCH') && (
                                  <>
                                    <td className="py-1.5 px-2 text-right font-mono">{a.entry ? fmt(a.entry, 2) : '--'}</td>
                                    <td className="py-1.5 px-2 text-right font-mono text-red-400">{a.stop ? fmt(a.stop, 2) : '--'}</td>
                                    <td className="py-1.5 px-2 text-right font-mono text-green-400">{a.target ? fmt(a.target, 2) : '--'}</td>
                                    <td className={clsx('py-1.5 px-2 text-right font-mono font-bold',
                                      (a.rr_ratio || 0) >= 1.5 ? 'text-green-400' : 'text-amber-400'
                                    )}>{a.rr_ratio ? fmt(a.rr_ratio, 1) : '--'}</td>
                                  </>
                                )}
                                <td className="py-1.5 px-2 text-nzt-muted truncate max-w-[200px]">{a.reason || a.lane_reason || ''}</td>
                              </tr>
                            ))}
                          </tbody>
                        </table>
                      </div>
                    </div>
                  )
                })}

                {/* Sector Rotation Summary in Lane View */}
                {sectorRotation?.sectors && sectorRotation.sectors.length > 0 && (
                  <Card title="SECTOR ROTATION CONTEXT">
                    <div className="grid grid-cols-3 gap-2">
                      {sectorRotation.sectors.slice(0, 9).map((s: any, i: number) => (
                        <div key={i} className={clsx(
                          'flex items-center justify-between p-2 rounded border text-xs',
                          s.rotation_signal === 'INFLOW' ? 'border-green-900/30 bg-green-900/10' :
                          s.rotation_signal === 'OUTFLOW' ? 'border-red-900/30 bg-red-900/10' :
                          'border-nzt-border bg-nzt-bg'
                        )}>
                          <span className="font-bold truncate">{s.sector}</span>
                          <div className="flex items-center gap-2">
                            <SectorSignalBadge signal={s.rotation_signal || 'NEUTRAL'} />
                            <span className="font-mono text-nzt-muted">{fmt(s.composite_score || 0, 0)}</span>
                          </div>
                        </div>
                      ))}
                    </div>
                  </Card>
                )}
              </div>
            )}

            {/* Ticker/Fund buttons grid — renders rows of 6, with expanded panel injected after the row containing the selected ticker */}
            {universeTab !== 'lanes' && (() => {
              const items = universeTab === 'us' ? tickerGrid : isaFunds.map(f => ({
                ticker: f.ticker, price: 0, change_pct: 0, volume: 0, rvol: 0,
                has_position: false, direction: null, label: f.label, type: f.type,
              }))
              // Split into rows of 6
              const rows: any[][] = []
              for (let i = 0; i < items.length; i += 6) {
                rows.push(items.slice(i, i + 6))
              }
              // Find which row contains the expanded ticker
              const expandedRowIdx = expandedTicker
                ? rows.findIndex(row => row.some((t: any) => t.ticker === expandedTicker))
                : -1

              return rows.map((row, rowIdx) => (
                <div key={`row-${rowIdx}`}>
                  {/* Row of 6 ticker buttons */}
                  <div className="grid grid-cols-6 gap-2 mb-2">
                    {row.map((t: any) => (
                      <button
                        key={t.ticker}
                        onClick={() => handleTickerClick(t.ticker)}
                        className={clsx(
                          'p-3 rounded-lg border text-center transition-all',
                          'hover:ring-1 hover:ring-nzt-accent/50 hover:scale-[1.02]',
                          expandedTicker === t.ticker
                            ? 'border-nzt-accent bg-nzt-accent/10 ring-1 ring-nzt-accent/30'
                            : universeTab === 'us'
                            ? clsx(
                                getHeatmapBg(t.change_pct),
                                t.has_position && t.direction === 'LONG'
                                  ? 'border-green-500 shadow-[0_0_12px_rgba(0,255,136,0.25)]'
                                  : t.has_position && t.direction === 'SHORT'
                                  ? 'border-red-500 shadow-[0_0_12px_rgba(255,68,68,0.25)]'
                                  : 'border-nzt-border'
                              )
                            : clsx(
                                'border-nzt-border',
                                t.type === 'long' ? 'bg-green-900/10' : t.type === 'short' ? 'bg-red-900/10' : 'bg-nzt-card'
                              )
                        )}
                      >
                        <div className="font-bold text-sm">{t.ticker}</div>
                        {universeTab === 'us' ? (
                          <>
                            <div className="text-lg font-mono font-bold mt-0.5">
                              {t.price > 0 ? `$${t.price.toFixed(2)}` : '--'}
                            </div>
                            <div className={clsx(
                              'text-xs font-mono',
                              t.change_pct > 0 ? 'text-green-400' :
                              t.change_pct < 0 ? 'text-red-400' : 'text-nzt-muted'
                            )}>
                              {t.change_pct > 0 ? '+' : ''}{(t.change_pct || 0).toFixed(2)}%
                            </div>
                            <div className="text-[10px] text-nzt-muted mt-0.5">
                              RVOL {t.rvol > 0 ? t.rvol.toFixed(1) : '--'}
                            </div>
                            {/* Liquidity Tier Badge */}
                            {(() => {
                              try {
                                const vol = t.volume || 0
                                const tier = vol >= 10_000_000 ? 1 : vol >= 2_000_000 ? 2 : vol >= 500_000 ? 3 : 4
                                const tierConfig: Record<number, { label: string; color: string }> = {
                                  1: { label: 'T1', color: 'bg-green-900/40 text-green-400 border-green-700/50' },
                                  2: { label: 'T2', color: 'bg-yellow-900/40 text-yellow-400 border-yellow-700/50' },
                                  3: { label: 'T3', color: 'bg-orange-900/40 text-orange-400 border-orange-700/50' },
                                  4: { label: 'T4', color: 'bg-red-900/40 text-red-400 border-red-700/50' },
                                }
                                const cfg = tierConfig[tier]
                                return (
                                  <span
                                    className={clsx('text-[9px] font-bold px-1.5 py-0.5 rounded border mt-0.5 inline-block', cfg.color)}
                                    title={`Liquidity Tier ${tier}: ${vol >= 10_000_000 ? '10M+' : vol >= 2_000_000 ? '2M-10M' : vol >= 500_000 ? '500K-2M' : '<500K'} volume`}
                                  >
                                    {cfg.label}
                                  </span>
                                )
                              } catch { return null }
                            })()}
                            {t.has_position && <DirBadge dir={t.direction || 'LONG'} />}
                          </>
                        ) : (
                          <div className="text-[10px] text-nzt-muted mt-1">{t.label}</div>
                        )}
                        <div className={clsx(
                          'text-[10px] mt-1',
                          expandedTicker === t.ticker ? 'text-nzt-accent' : 'text-nzt-muted'
                        )}>
                          {expandedTicker === t.ticker ? '▲ COLLAPSE' : '▼ EXPAND'}
                        </div>
                      </button>
                    ))}
                  </div>

                  {/* Expanded panel — full width, inserted directly after the row containing the clicked ticker */}
                  {expandedRowIdx === rowIdx && expandedTicker && (
                    <>
                      {loadingTicker === expandedTicker ? (
                        <div className="mb-2 bg-nzt-card rounded-lg border border-nzt-accent/30 p-8 text-center">
                          <div className="text-nzt-accent text-sm animate-pulse">
                            Loading institutional data for {expandedTicker}...
                          </div>
                        </div>
                      ) : tickerDetails[expandedTicker] ? (
                        <div className="mb-2">
                          <TickerExpandedPanel
                            ticker={expandedTicker}
                            data={tickerDetails[expandedTicker]}
                            isLoading={false}
                          />
                        </div>
                      ) : null}
                    </>
                  )}
                </div>
              ))
            })()}
          </div>

          {/* Section 3: Strategy Performance Table */}
          <div className="mt-4">
            <Card title="STRATEGY PERFORMANCE">
              {sortedStrategies.length === 0 ? (
                <p className="text-nzt-muted text-sm text-center py-6">
                  Awaiting first signals...
                </p>
              ) : (
                <div className="overflow-x-auto">
                  <table className="w-full text-sm">
                    <thead>
                      <tr className="border-b border-nzt-border">
                        {([
                          ['strategy', 'Strategy'],
                          ['trades', 'Trades'],
                          ['win_rate', 'Win Rate'],
                          ['total_pnl', 'Total P&L'],
                          ['avg_r', 'Avg R'],
                          ['status', 'Status'],
                        ] as [SortKey, string][]).map(([key, label]) => (
                          <th
                            key={key}
                            onClick={() => handleSort(key)}
                            className="py-2 px-3 text-left text-nzt-muted font-semibold uppercase text-xs tracking-wider cursor-pointer hover:text-nzt-text select-none"
                          >
                            {label}
                            {sortKey === key && (
                              <span className="ml-1 text-nzt-accent">
                                {sortDir === 'asc' ? '^' : 'v'}
                              </span>
                            )}
                          </th>
                        ))}
                      </tr>
                    </thead>
                    <tbody>
                      {sortedStrategies.map((s) => (
                        <tr
                          key={s.strategy}
                          className="border-b border-nzt-border/50 hover:bg-nzt-bg/50 transition"
                        >
                          <td className="py-2 px-3 font-bold">{s.strategy}</td>
                          <td className="py-2 px-3 font-mono">{s.trades}</td>
                          <td className="py-2 px-3 font-mono">
                            <span className={clsx(
                              s.win_rate >= 60 ? 'text-nzt-accent' :
                              s.win_rate >= 40 ? 'text-nzt-warning' : 'text-nzt-danger'
                            )}>
                              {s.win_rate.toFixed(1)}%
                            </span>
                          </td>
                          <td className={clsx(
                            'py-2 px-3 font-mono font-bold',
                            s.total_pnl >= 0 ? 'text-nzt-accent' : 'text-nzt-danger'
                          )}>
                            {s.total_pnl >= 0 ? '+' : ''}${s.total_pnl.toLocaleString('en-US', { minimumFractionDigits: 2 })}
                          </td>
                          <td className={clsx(
                            'py-2 px-3 font-mono',
                            s.avg_r >= 0 ? 'text-nzt-accent' : 'text-nzt-danger'
                          )}>
                            {s.avg_r >= 0 ? '+' : ''}{s.avg_r.toFixed(2)}R
                          </td>
                          <td className="py-2 px-3">
                            <span className={clsx(
                              'text-xs px-2 py-0.5 rounded',
                              s.status === 'ACTIVE' ? 'bg-green-900/30 text-green-400' :
                              s.status === 'PAUSED' ? 'bg-yellow-900/30 text-yellow-400' :
                              'bg-red-900/30 text-red-400'
                            )}>
                              {s.status}
                            </span>
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              )}
            </Card>
          </div>

          {/* === REGIME PERFORMANCE MATRIX === */}
          {(() => {
            try {
              const cells = regimeMatrix?.cells || regimeMatrix?.matrix
              if (!cells || !Array.isArray(cells) || cells.length === 0) return null
              // Extract unique strategies and regimes
              const strategiesSet = new Set<string>()
              const regimesSet = new Set<string>()
              cells.forEach((c: any) => {
                if (c.strategy) strategiesSet.add(c.strategy)
                if (c.regime) regimesSet.add(c.regime)
              })
              const stratList = Array.from(strategiesSet)
              const regimeList = Array.from(regimesSet)
              const lookup = (strat: string, regime: string) =>
                cells.find((c: any) => c.strategy === strat && c.regime === regime)

              return (
                <div className="mt-4">
                  <Card title="REGIME PERFORMANCE MATRIX">
                    <div className="text-[10px] text-nzt-muted mb-2">
                      Strategy performance broken down by market regime. Green = profitable, Red = losing. Size shows sample count.
                    </div>
                    <div className="overflow-x-auto">
                      <table className="w-full text-xs">
                        <thead>
                          <tr className="border-b border-nzt-border text-nzt-muted">
                            <th className="py-1.5 px-2 text-left font-bold">Strategy</th>
                            {regimeList.map(r => (
                              <th key={r} className={clsx('py-1.5 px-2 text-center font-bold', getRegimeColor(r))}>
                                {r}
                              </th>
                            ))}
                          </tr>
                        </thead>
                        <tbody>
                          {stratList.map(strat => (
                            <tr key={strat} className="border-b border-nzt-border/30">
                              <td className="py-1.5 px-2 font-bold text-nzt-text">{strat}</td>
                              {regimeList.map(regime => {
                                const cell = lookup(strat, regime)
                                if (!cell || (cell.sample_size || cell.trades || 0) === 0) {
                                  return (
                                    <td key={regime} className="py-1.5 px-2 text-center text-nzt-muted">
                                      <span className="text-[10px]">--</span>
                                    </td>
                                  )
                                }
                                const wr = cell.win_rate ?? 0
                                const avgR = cell.avg_r ?? 0
                                const n = cell.sample_size || cell.trades || 0
                                const isProfitable = avgR >= 0
                                return (
                                  <td
                                    key={regime}
                                    className={clsx(
                                      'py-1.5 px-2 text-center rounded',
                                      isProfitable ? 'bg-green-900/20' : 'bg-red-900/20'
                                    )}
                                    title={`${strat} in ${regime}: WR ${fmt(wr, 1)}%, Avg R ${fmt(avgR, 2)}, n=${n}`}
                                  >
                                    <div className={clsx('font-mono font-bold', isProfitable ? 'text-nzt-accent' : 'text-nzt-danger')}>
                                      {fmt(wr, 0)}%
                                    </div>
                                    <div className={clsx('font-mono text-[10px]', isProfitable ? 'text-green-400' : 'text-red-400')}>
                                      {avgR >= 0 ? '+' : ''}{fmt(avgR, 2)}R
                                    </div>
                                    <div className="text-[9px] text-nzt-muted">n={n}</div>
                                  </td>
                                )
                              })}
                            </tr>
                          ))}
                        </tbody>
                      </table>
                    </div>
                  </Card>
                </div>
              )
            } catch { return null }
          })()}

          {/* Section 4: Risk Dashboard Row */}
          <div className="grid grid-cols-3 gap-4 mt-4">
            <Card title="EQUITY">
              <div className="space-y-3">
                <div className="flex justify-between items-baseline">
                  <span className="text-nzt-muted text-sm">Current</span>
                  <span className="text-2xl font-bold font-mono">
                    ${(risk?.current_equity ?? 0).toLocaleString('en-US', { minimumFractionDigits: 2 })}
                  </span>
                </div>
                <div className="flex justify-between items-baseline">
                  <span className="text-nzt-muted text-sm">Peak</span>
                  <span className="text-sm font-mono text-nzt-text">
                    ${(risk?.peak_equity ?? 0).toLocaleString('en-US', { minimumFractionDigits: 2 })}
                  </span>
                </div>
                <div className="flex justify-between items-baseline">
                  <span className="text-nzt-muted text-sm">Drawdown</span>
                  <span className={clsx(
                    'text-sm font-mono font-bold',
                    (risk?.drawdown_pct ?? 0) > 5 ? 'text-nzt-danger' :
                    (risk?.drawdown_pct ?? 0) > 2 ? 'text-nzt-warning' : 'text-nzt-accent'
                  )}>
                    -{(risk?.drawdown_pct ?? 0).toFixed(2)}%
                  </span>
                </div>
              </div>
            </Card>

            <Card title="DAILY P&L">
              <div className="text-center">
                <div className={clsx(
                  'text-3xl font-bold font-mono',
                  (risk?.daily_pnl ?? 0) >= 0 ? 'text-nzt-accent' : 'text-nzt-danger'
                )}>
                  {(risk?.daily_pnl ?? 0) >= 0 ? '+' : ''}
                  ${(risk?.daily_pnl ?? 0).toLocaleString('en-US', { minimumFractionDigits: 2 })}
                </div>
                <div className="mt-3 h-2 bg-nzt-bg rounded-full overflow-hidden">
                  <div
                    className={clsx(
                      'h-full rounded-full transition-all',
                      (risk?.daily_pnl ?? 0) >= 0 ? 'bg-nzt-accent' : 'bg-nzt-danger'
                    )}
                    style={{
                      width: `${Math.min(Math.abs(risk?.daily_pnl ?? 0) / 10, 100)}%`,
                      marginLeft: (risk?.daily_pnl ?? 0) < 0 ? 'auto' : undefined,
                    }}
                  />
                </div>
                <div className="flex justify-between text-[10px] text-nzt-muted mt-1">
                  <span>-$1000</span>
                  <span>$0</span>
                  <span>+$1000</span>
                </div>
              </div>
            </Card>

            <Card title="RISK BUDGET">
              <div className="space-y-3">
                <div className="flex justify-between text-sm">
                  <span className="text-nzt-muted">Used</span>
                  <span className="font-mono font-bold">
                    ${(risk?.risk_budget_used ?? 0).toLocaleString('en-US', { minimumFractionDigits: 2 })}
                  </span>
                </div>
                <div className="flex justify-between text-sm">
                  <span className="text-nzt-muted">Total</span>
                  <span className="font-mono">
                    ${(risk?.open_risk_dollars ?? 0).toLocaleString('en-US', { minimumFractionDigits: 2 })}
                  </span>
                </div>
                <div className="h-3 bg-nzt-bg rounded-full overflow-hidden">
                  <div
                    className={clsx(
                      'h-full rounded-full transition-all',
                      getRiskBudgetColor(risk?.risk_budget_used ?? 0, risk?.open_risk_dollars ?? 1)
                    )}
                    style={{
                      width: `${Math.min(
                        ((risk?.risk_budget_used ?? 0) / (risk?.open_risk_dollars || 1)) * 100,
                        100
                      )}%`,
                    }}
                  />
                </div>
                <div className="text-center text-xs text-nzt-muted">
                  {(risk?.current_equity ?? 0) > 0
                    ? (((risk?.open_risk_dollars ?? 0) / ((risk?.current_equity ?? 10000) * 0.03)) * 100).toFixed(1)
                    : '0.0'}% utilized
                </div>
              </div>
            </Card>
          </div>

          {/* Section 5: Multi-Timeframe Heatmap */}
          {heatmapData.length > 0 && (
            <div className="mt-4">
              <Card title="MULTI-TIMEFRAME HEATMAP (Day / Week / Month)">
                <div className="overflow-x-auto">
                  <table className="w-full text-sm">
                    <thead>
                      <tr className="border-b border-nzt-border">
                        <th className="py-2 px-3 text-left text-nzt-muted text-xs">Ticker</th>
                        <th className="py-2 px-3 text-right text-nzt-muted text-xs">Price</th>
                        <th className="py-2 px-3 text-right text-nzt-muted text-xs">Day</th>
                        <th className="py-2 px-3 text-right text-nzt-muted text-xs">Week</th>
                        <th className="py-2 px-3 text-right text-nzt-muted text-xs">Month</th>
                      </tr>
                    </thead>
                    <tbody>
                      {heatmapData.map((h: any) => (
                        <tr key={h.ticker} className="border-b border-nzt-border/30 hover:bg-nzt-bg/50">
                          <td className="py-1.5 px-3"><TickerLink ticker={h.ticker} className="text-sm" /></td>
                          <td className="py-1.5 px-3 text-right font-mono">${h.price?.toFixed(2)}</td>
                          <td className={clsx('py-1.5 px-3 text-right font-mono', h.day_change >= 0 ? 'text-green-400' : 'text-red-400')}>
                            {h.day_change >= 0 ? '+' : ''}{h.day_change?.toFixed(2)}%
                          </td>
                          <td className={clsx('py-1.5 px-3 text-right font-mono', h.week_change >= 0 ? 'text-green-400' : 'text-red-400')}>
                            {h.week_change >= 0 ? '+' : ''}{h.week_change?.toFixed(2)}%
                          </td>
                          <td className={clsx('py-1.5 px-3 text-right font-mono', h.month_change >= 0 ? 'text-green-400' : 'text-red-400')}>
                            {h.month_change >= 0 ? '+' : ''}{h.month_change?.toFixed(2)}%
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              </Card>
            </div>
          )}

          {/* Section 6: Equity Curve */}
          {equitySnapshots?.snapshots?.length > 0 && (
            <div className="mt-4">
              <Card title="EQUITY CURVE (90 Days)">
                <div className="h-32 flex items-end gap-0.5">
                  {equitySnapshots.snapshots.map((s: any, i: number) => {
                    const base = 10000
                    const eq = s.ending_equity || base
                    const pct = ((eq - base) / base) * 100
                    const height = Math.max(2, Math.min(Math.abs(pct) * 5 + 20, 100))
                    return (
                      <div key={i} className="flex-1 flex flex-col justify-end" title={`${s.date}: $${eq.toFixed(2)}`}>
                        <div
                          className={clsx('rounded-t-sm min-w-[2px]', pct >= 0 ? 'bg-nzt-accent/60' : 'bg-nzt-danger/60')}
                          style={{ height: `${height}%` }}
                        />
                      </div>
                    )
                  })}
                </div>
              </Card>
            </div>
          )}

          {/* Section 7: Recent Activity Feed */}
          <div className="mt-4">
            <Card title="RECENT ACTIVITY">
              <div className="space-y-2 max-h-80 overflow-y-auto">
                {sortedStrategies.length === 0 && tickers.length === 0 ? (
                  <p className="text-nzt-muted text-sm text-center py-6">
                    Awaiting first signals...
                  </p>
                ) : (
                  <ActivityFeed />
                )}
              </div>
            </Card>
          </div>
        </>
      )}
    </div>
  )
}

// === EXPANDED TICKER PANEL — Institutional-Grade Stats ===
// Stat row helper — stacks label on top, value below for clean layout
function StatRow({ label, value, color }: { label: string; value: string | number; color?: string }) {
  return (
    <div className="flex justify-between items-baseline gap-2">
      <span className="text-nzt-muted text-[11px] shrink-0">{label}</span>
      <span className={clsx('font-mono text-[11px] font-bold text-right truncate', color || 'text-nzt-text')}>{value}</span>
    </div>
  )
}

function TickerExpandedPanel({ ticker, data, isLoading }: { ticker: string; data: any; isLoading: boolean }) {
  if (isLoading || !data) return null
  if (data.error) {
    return (
      <div className="bg-nzt-card rounded-lg border border-nzt-border p-4 text-center text-nzt-muted text-sm">
        Failed to load data for {ticker}
      </div>
    )
  }

  const p = data.performance || {}
  const rsiVal = data.rsi14 ?? 50
  const rsiColor = rsiVal > 70 ? 'text-red-400' : rsiVal < 30 ? 'text-green-400' : 'text-yellow-400'
  const rsiLabel = rsiVal > 70 ? 'OB' : rsiVal < 30 ? 'OS' : ''
  const alignColor = data.ema_alignment === 'BULLISH' ? 'text-green-400' : data.ema_alignment === 'BEARISH' ? 'text-red-400' : 'text-yellow-400'
  const pnlColor = (v: number) => v >= 0 ? 'text-green-400' : 'text-red-400'

  const fmtMcap = (n: number) => {
    if (!n) return '--'
    if (n >= 1e12) return `$${(n / 1e12).toFixed(1)}T`
    if (n >= 1e9) return `$${(n / 1e9).toFixed(1)}B`
    if (n >= 1e6) return `$${(n / 1e6).toFixed(0)}M`
    return `$${n.toLocaleString()}`
  }

  const fmtVol = (n: number) => {
    if (!n) return '--'
    if (n >= 1e9) return `${(n / 1e9).toFixed(1)}B`
    if (n >= 1e6) return `${(n / 1e6).toFixed(0)}M`
    return `${(n / 1e3).toFixed(0)}K`
  }

  const fmtNum = (n: number | undefined, d = 2) => n != null ? n.toFixed(d) : '--'

  return (
    <div className="bg-nzt-card rounded-lg border border-nzt-accent/30 p-5 space-y-4">
      {/* Header row */}
      <div className="flex items-center justify-between flex-wrap gap-2">
        <div className="flex items-center gap-4">
          <span className="text-2xl font-bold text-nzt-accent">{ticker}</span>
          <span className="text-3xl font-bold font-mono">${fmtNum(data.price)}</span>
          <span className={clsx('text-lg font-mono font-bold', pnlColor(data.change_pct ?? 0))}>
            {(data.change_pct ?? 0) >= 0 ? '+' : ''}{fmtNum(data.change_pct)}%
          </span>
          <span className="text-sm text-nzt-muted">Mkt Cap: {fmtMcap(data.market_cap)}</span>
        </div>
        <div className="flex items-center gap-2">
          {data.isa_mapping?.LONG && (
            <span className="text-[10px] bg-green-900/40 text-green-400 px-2 py-0.5 rounded font-bold">
              ISA: {data.isa_mapping.LONG}
            </span>
          )}
          {data.isa_mapping?.SHORT && (
            <span className="text-[10px] bg-red-900/40 text-red-400 px-2 py-0.5 rounded font-bold">
              SHORT: {data.isa_mapping.SHORT}
            </span>
          )}
          <Link
            href={`/ticker/${encodeURIComponent(ticker)}`}
            className="text-xs bg-nzt-accent/20 text-nzt-accent px-3 py-1 rounded font-bold hover:bg-nzt-accent/30 transition"
          >
            FULL PAGE &rarr;
          </Link>
        </div>
      </div>

      {/* AI Day Trading Idea */}
      {data.ai_idea && (
        <div className="bg-gradient-to-r from-purple-900/30 to-blue-900/20 border border-purple-500/30 rounded-lg p-3">
          <div className="flex items-center gap-2 mb-1">
            <span className="text-[10px] bg-purple-500/30 text-purple-300 px-2 py-0.5 rounded font-bold">AI IDEA</span>
            <span className="text-[10px] text-nzt-muted">Gemini Flash Analysis</span>
          </div>
          <p className="text-sm text-nzt-text leading-relaxed">{data.ai_idea}</p>
        </div>
      )}

      {/* 4-column institutional stats — using table-like layout with min-width */}
      <div className="grid grid-cols-4 gap-4 min-w-0">

        {/* Column 1: Price Structure */}
        <div className="bg-nzt-bg rounded-lg p-3 min-w-0">
          <h4 className="text-[10px] font-bold text-nzt-accent uppercase tracking-wider mb-3 border-b border-nzt-border/50 pb-1.5">Price Structure</h4>
          <div className="space-y-1.5">
            <StatRow label="VWAP" value={`$${fmtNum(data.vwap)}`} />
            <StatRow label="vs VWAP" value={`${(data.vwap_pct ?? 0) >= 0 ? '+' : ''}${fmtNum(data.vwap_pct)}%`} color={pnlColor(data.vwap_pct ?? 0)} />
            <div className="border-t border-nzt-border/30 my-1" />
            <StatRow label="EMA 9" value={`$${fmtNum(data.ema9)}`} />
            <StatRow label="EMA 20" value={`$${fmtNum(data.ema20)}`} />
            <StatRow label="EMA 50" value={`$${fmtNum(data.ema50)}`} />
            {data.ema200 > 0 && <StatRow label="EMA 200" value={`$${fmtNum(data.ema200)}`} />}
            <div className="border-t border-nzt-border/30 my-1" />
            <StatRow label="Alignment" value={data.ema_alignment ?? 'N/A'} color={alignColor} />
          </div>
        </div>

        {/* Column 2: Volatility & Range */}
        <div className="bg-nzt-bg rounded-lg p-3 min-w-0">
          <h4 className="text-[10px] font-bold text-nzt-accent uppercase tracking-wider mb-3 border-b border-nzt-border/50 pb-1.5">Volatility & Range</h4>
          <div className="space-y-1.5">
            <StatRow label="ATR (14)" value={`$${fmtNum(data.atr14)}`} />
            <StatRow label="ATR %" value={`${fmtNum(data.atr_pct, 1)}%`} color={(data.atr_pct ?? 0) > 3 ? 'text-orange-400' : undefined} />
            <StatRow label="ADX" value={`${fmtNum(data.adx, 0)} ${(data.adx ?? 0) > 25 ? 'TREND' : 'FLAT'}`} color={(data.adx ?? 0) > 25 ? 'text-green-400' : 'text-nzt-muted'} />
            <div className="border-t border-nzt-border/30 my-1" />
            <StatRow label="Day Hi" value={`$${fmtNum(data.day_high)}`} />
            <StatRow label="Day Lo" value={`$${fmtNum(data.day_low)}`} />
            <StatRow label="BB Upper" value={`$${fmtNum(data.bb_upper)}`} />
            <StatRow label="BB Lower" value={`$${fmtNum(data.bb_lower)}`} />
            <StatRow label="BB %" value={`${fmtNum(data.bb_pct, 0)}%`} color={(data.bb_pct ?? 50) > 80 ? 'text-red-400' : (data.bb_pct ?? 50) < 20 ? 'text-green-400' : 'text-yellow-400'} />
            <div className="border-t border-nzt-border/30 my-1" />
            <StatRow label="52W Hi" value={`$${fmtNum(data.high_52w)}`} />
            <StatRow label="vs 52W" value={`${fmtNum(data.pct_from_52w_high, 1)}%`} color="text-red-400" />
          </div>
        </div>

        {/* Column 3: Momentum & Volume */}
        <div className="bg-nzt-bg rounded-lg p-3 min-w-0">
          <h4 className="text-[10px] font-bold text-nzt-accent uppercase tracking-wider mb-3 border-b border-nzt-border/50 pb-1.5">Momentum & Volume</h4>
          <div className="space-y-1.5">
            <StatRow label="RSI (14)" value={`${fmtNum(data.rsi14, 0)}${rsiLabel ? ` ${rsiLabel}` : ''}`} color={rsiColor} />
            <StatRow label="Stoch RSI" value={fmtNum(data.stoch_rsi, 0)} color={(data.stoch_rsi ?? 50) > 80 ? 'text-red-400' : (data.stoch_rsi ?? 50) < 20 ? 'text-green-400' : undefined} />
            <StatRow label="MACD Hist" value={fmtNum(data.macd_hist, 3)} color={pnlColor(data.macd_hist ?? 0)} />
            <StatRow label="MACD Sig" value={fmtNum(data.macd_signal, 3)} />
            <div className="border-t border-nzt-border/30 my-1" />
            <StatRow label="RVOL" value={`${fmtNum(data.rvol, 1)}x`} color={(data.rvol ?? 0) >= 2 ? 'text-green-400' : (data.rvol ?? 0) >= 1.3 ? 'text-yellow-400' : 'text-nzt-muted'} />
            <StatRow label="Volume" value={`${((data.volume ?? 0) / 1e6).toFixed(1)}M`} />
            <StatRow label="Avg Vol" value={`${((data.avg_volume_20d ?? 0) / 1e6).toFixed(1)}M`} />
            <StatRow label="$ Volume" value={fmtVol(data.dollar_volume)} />
          </div>
        </div>

        {/* Column 4: Trading Performance */}
        <div className="bg-nzt-bg rounded-lg p-3 min-w-0">
          <h4 className="text-[10px] font-bold text-nzt-accent uppercase tracking-wider mb-3 border-b border-nzt-border/50 pb-1.5">Trading Performance</h4>
          {(p.total_trades ?? 0) > 0 ? (
            <div className="space-y-1.5">
              <StatRow label="Win Rate" value={`${fmtNum(p.win_rate, 1)}%`} color={(p.win_rate ?? 0) >= 55 ? 'text-green-400' : 'text-red-400'} />
              <StatRow label="Trades" value={`${p.wins}W/${p.losses}L`} />
              <StatRow label="P&L" value={`${(p.total_pnl ?? 0) >= 0 ? '+' : ''}$${fmtNum(p.total_pnl)}`} color={pnlColor(p.total_pnl ?? 0)} />
              <StatRow label="Avg R" value={`${(p.avg_r ?? 0) >= 0 ? '+' : ''}${fmtNum(p.avg_r)}R`} color={pnlColor(p.avg_r ?? 0)} />
              <StatRow label="PF" value={fmtNum(p.profit_factor)} color={(p.profit_factor ?? 0) >= 1.5 ? 'text-green-400' : (p.profit_factor ?? 0) >= 1.0 ? 'text-yellow-400' : 'text-red-400'} />
              <div className="border-t border-nzt-border/30 my-1" />
              <StatRow label="Best" value={`+${fmtNum(p.best_r, 1)}R`} color="text-green-400" />
              <StatRow label="Worst" value={`${fmtNum(p.worst_r, 1)}R`} color="text-red-400" />
              <StatRow label="Avg Hold" value={`${(p.avg_duration_min ?? 0).toFixed(0)}m`} />
            </div>
          ) : (
            <div className="text-center text-nzt-muted text-xs py-6">No trades yet</div>
          )}
        </div>
      </div>

      {/* Bottom row: Strategy breakdown + Recent trades */}
      {(p.by_strategy?.length > 0 || p.recent_trades?.length > 0) && (
        <div className="grid grid-cols-2 gap-4">
          {p.by_strategy?.length > 0 && (
            <div className="bg-nzt-bg rounded-lg p-3">
              <h4 className="text-[10px] font-bold text-nzt-accent uppercase tracking-wider mb-2 border-b border-nzt-border/50 pb-1">By Strategy</h4>
              <div className="space-y-1">
                {p.by_strategy.map((s: any) => (
                  <div key={s.strategy} className="grid grid-cols-5 gap-1 text-[11px] items-center">
                    <span className="font-bold">{s.strategy}</span>
                    <span className="font-mono text-nzt-muted text-center">{s.trades}t</span>
                    <span className={clsx('font-mono text-center', s.win_rate >= 55 ? 'text-green-400' : 'text-red-400')}>
                      {s.win_rate.toFixed(0)}%
                    </span>
                    <span className={clsx('font-mono font-bold text-right', pnlColor(s.pnl ?? 0))}>
                      {s.pnl >= 0 ? '+' : ''}${s.pnl.toFixed(0)}
                    </span>
                    <span className={clsx('font-mono text-right', pnlColor(s.avg_r ?? 0))}>
                      {s.avg_r >= 0 ? '+' : ''}{s.avg_r?.toFixed(1)}R
                    </span>
                  </div>
                ))}
              </div>
            </div>
          )}

          {p.recent_trades?.length > 0 && (
            <div className="bg-nzt-bg rounded-lg p-3">
              <h4 className="text-[10px] font-bold text-nzt-accent uppercase tracking-wider mb-2 border-b border-nzt-border/50 pb-1">Last 5 Trades</h4>
              <div className="space-y-1">
                {p.recent_trades.map((t: any, i: number) => (
                  <div key={i} className="grid grid-cols-5 gap-1 text-[11px] items-center">
                    <DirBadge dir={t.direction || 'LONG'} />
                    <span className="font-bold text-nzt-muted text-center">{t.strategy}</span>
                    <span className={clsx('font-mono font-bold text-center', pnlColor(t.r_multiple ?? 0))}>
                      {(t.r_multiple ?? 0) >= 0 ? '+' : ''}{t.r_multiple?.toFixed(1)}R
                    </span>
                    <span className={clsx('font-mono text-right', pnlColor(t.net_pnl ?? 0))}>
                      {(t.net_pnl ?? 0) >= 0 ? '+' : ''}${t.net_pnl?.toFixed(0)}
                    </span>
                    <span className="text-[10px] text-nzt-muted text-right truncate">{t.exit_reason}</span>
                  </div>
                ))}
              </div>
            </div>
          )}
        </div>
      )}

      {/* Open position alert */}
      {p.open_position && (
        <div className="bg-green-900/20 border border-green-500/30 rounded-lg p-3 flex items-center justify-between">
          <div className="flex items-center gap-3">
            <span className="text-[10px] bg-green-500/30 text-green-400 px-2 py-0.5 rounded font-bold animate-pulse">
              LIVE
            </span>
            <DirBadge dir={p.open_position.direction || 'LONG'} />
            <span className="text-xs font-mono">
              {p.open_position.shares} @ ${p.open_position.entry_price?.toFixed(2)}
            </span>
          </div>
          <div className="flex items-center gap-4 text-xs font-mono">
            <span className="text-nzt-muted">Stop ${p.open_position.current_stop?.toFixed(2)}</span>
            <span className={clsx('font-bold', pnlColor(p.open_position.unrealised_pnl ?? 0))}>
              {(p.open_position.unrealised_pnl ?? 0) >= 0 ? '+' : ''}${p.open_position.unrealised_pnl?.toFixed(2)}
            </span>
          </div>
        </div>
      )}
    </div>
  )
}

// === Activity Feed Component ===
function ActivityFeed() {
  const [activities, setActivities] = useState<ActivityItem[]>([])

  interface ActivityItem {
    time: string
    ticker: string
    direction: string
    strategy: string
    result: string
  }

  useEffect(() => {
    async function load() {
      try {
        const sigs = await fetchAPI<any[]>('/api/signals?hours=24&limit=15')
        const items: ActivityItem[] = sigs.map((s) => ({
          time: new Date(s.timestamp).toLocaleTimeString(),
          ticker: s.ticker,
          direction: s.direction,
          strategy: s.strategy,
          result: s.status,
        }))
        setActivities(items)
      } catch {
        // API not available yet
      }
    }
    load()
    const interval = setInterval(load, 60000)
    return () => clearInterval(interval)
  }, [])

  if (activities.length === 0) {
    return (
      <p className="text-nzt-muted text-sm text-center py-6">
        Awaiting first signals...
      </p>
    )
  }

  return (
    <>
      {activities.map((a, i) => (
        <div
          key={`${a.ticker}-${a.time}-${i}`}
          className="flex items-center justify-between p-2 bg-nzt-bg rounded border border-nzt-border"
        >
          <div className="flex items-center gap-3">
            <span className="text-xs font-mono text-nzt-muted w-16">{a.time}</span>
            <DirBadge dir={a.direction} />
            <TickerLink ticker={a.ticker} className="text-sm" />
            <span className="text-xs text-nzt-muted">{a.strategy}</span>
          </div>
          <StatusBadge status={a.result} />
        </div>
      ))}
    </>
  )
}
