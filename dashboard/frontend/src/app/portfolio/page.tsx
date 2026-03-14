'use client'

import { useState, useEffect, useCallback, useMemo } from 'react'
import clsx from 'clsx'
import { Card, NavTabs, TickerLink } from '../lib/components'
import { getAPI, fetchAPI, fmt, fmtDollar, fmtPct, timeAgo } from '../lib/api'

// === Types ===
type Tab = 'overview' | 'mfe-mae' | 'distribution' | 'compounding' | 'monte-carlo'

export default function PortfolioPage() {
  const [tab, setTab] = useState<Tab>('overview')
  const [loading, setLoading] = useState(true)

  // Data states
  const [holdings, setHoldings] = useState<any>(null)
  const [ratios, setRatios] = useState<any>(null)
  const [distribution, setDistribution] = useState<any>(null)
  const [mfeMae, setMfeMae] = useState<any>(null)
  const [compounding, setCompounding] = useState<any>(null)
  const [leagueTable, setLeagueTable] = useState<any>(null)

  const refresh = useCallback(async () => {
    setLoading(true)
    try {
      const results = await Promise.allSettled([
        fetchAPI<any>('/api/portfolio/holdings'),
        fetchAPI<any>('/api/portfolio/ratios'),
        fetchAPI<any>('/api/portfolio/distribution'),
        fetchAPI<any>('/api/portfolio/mfe-mae'),
        fetchAPI<any>('/api/compounding/progress'),
        fetchAPI<any>('/api/b-team/league-table'),
      ])
      const get = (i: number) => results[i].status === 'fulfilled' ? (results[i] as any).value : null
      setHoldings(get(0))
      setRatios(get(1))
      setDistribution(get(2))
      setMfeMae(get(3))
      setCompounding(get(4))
      setLeagueTable(get(5))
    } catch {}
    setLoading(false)
  }, [])

  useEffect(() => {
    refresh()
    const timer = setInterval(refresh, 30000)
    return () => clearInterval(timer)
  }, [refresh])

  const tabs = [
    { id: 'overview' as Tab, label: 'Overview' },
    { id: 'mfe-mae' as Tab, label: 'MFE/MAE' },
    { id: 'distribution' as Tab, label: 'R-Distribution' },
    { id: 'compounding' as Tab, label: 'Compounding' },
    { id: 'monte-carlo' as Tab, label: 'Monte Carlo' },
  ]

  // === Monte Carlo Simulation ===
  const monteCarloResults = useMemo(() => {
    try {
      const rMultiples: number[] = []
      if (distribution?.buckets && Array.isArray(distribution.buckets)) {
        // Reconstruct individual R-multiples from bucket distribution
        distribution.buckets.forEach((b: any) => {
          const midpoint = ((b.low ?? 0) + (b.high ?? 0)) / 2
          const count = b.count || 0
          for (let j = 0; j < count; j++) {
            rMultiples.push(midpoint)
          }
        })
      }
      // Also try raw points from distribution
      if (rMultiples.length === 0 && distribution?.trades && Array.isArray(distribution.trades)) {
        distribution.trades.forEach((t: any) => {
          if (t.r_multiple != null) rMultiples.push(t.r_multiple)
        })
      }
      if (rMultiples.length < 5) return null

      const SIMULATIONS = 1000
      const TRADES_PER_YEAR = 252
      const STARTING_EQUITY = 10000
      const RISK_PER_TRADE = 0.01 // 1% risk per trade

      // Run simulations
      const equityCurves: number[][] = []
      let doublingCount = 0

      for (let sim = 0; sim < SIMULATIONS; sim++) {
        let equity = STARTING_EQUITY
        const curve: number[] = [equity]
        for (let t = 0; t < TRADES_PER_YEAR; t++) {
          const rIdx = Math.floor(Math.random() * rMultiples.length)
          const rOutcome = rMultiples[rIdx]
          const riskAmount = equity * RISK_PER_TRADE
          equity += riskAmount * rOutcome
          equity = Math.max(equity, 0) // floor at 0
          curve.push(equity)
        }
        equityCurves.push(curve)
        if (equity >= STARTING_EQUITY * 2) doublingCount++
      }

      // Calculate percentile bands at each trade index
      const percentiles = [10, 25, 50, 75, 90]
      const bands: Record<number, number[]> = {}

      for (let t = 0; t <= TRADES_PER_YEAR; t++) {
        const equitiesAtT = equityCurves.map(c => c[t]).sort((a, b) => a - b)
        bands[t] = percentiles.map(p => {
          const idx = Math.floor((p / 100) * equitiesAtT.length)
          return equitiesAtT[Math.min(idx, equitiesAtT.length - 1)]
        })
      }

      // Sample every N trades for rendering
      const sampleInterval = Math.max(1, Math.floor(TRADES_PER_YEAR / 60))
      const sampledBands: { trade: number; p10: number; p25: number; p50: number; p75: number; p90: number }[] = []
      for (let t = 0; t <= TRADES_PER_YEAR; t += sampleInterval) {
        const b = bands[t]
        sampledBands.push({
          trade: t,
          p10: b[0],
          p25: b[1],
          p50: b[2],
          p75: b[3],
          p90: b[4],
        })
      }
      // Always include final point
      if (sampledBands[sampledBands.length - 1].trade !== TRADES_PER_YEAR) {
        const b = bands[TRADES_PER_YEAR]
        sampledBands.push({ trade: TRADES_PER_YEAR, p10: b[0], p25: b[1], p50: b[2], p75: b[3], p90: b[4] })
      }

      // Final stats
      const finalEquities = equityCurves.map(c => c[TRADES_PER_YEAR]).sort((a, b) => a - b)
      const median = finalEquities[Math.floor(finalEquities.length / 2)]
      const mean = finalEquities.reduce((s, v) => s + v, 0) / finalEquities.length
      const probDoubling = (doublingCount / SIMULATIONS) * 100
      const probRuin = (finalEquities.filter(e => e < STARTING_EQUITY * 0.5).length / SIMULATIONS) * 100

      return {
        bands: sampledBands,
        sampleSize: rMultiples.length,
        simulations: SIMULATIONS,
        tradesPerYear: TRADES_PER_YEAR,
        median,
        mean,
        probDoubling,
        probRuin,
        p10Final: finalEquities[Math.floor(0.1 * finalEquities.length)],
        p90Final: finalEquities[Math.floor(0.9 * finalEquities.length)],
      }
    } catch { return null }
  }, [distribution])

  return (
    <div className="min-h-screen p-4 max-w-[1920px] mx-auto">
      <header className="flex items-center justify-between mb-4">
        <div className="flex items-center gap-4">
          <h1 className="text-2xl font-bold tracking-tight">
            NZT-48 <span className="text-nzt-accent">Portfolio Analytics</span>
          </h1>
          {loading && <span className="text-[10px] text-nzt-muted animate-pulse">Refreshing...</span>}
        </div>
        <NavTabs />
      </header>

      {/* Sub-tabs */}
      <div className="flex gap-2 mb-4">
        {tabs.map(t => (
          <button
            key={t.id}
            onClick={() => setTab(t.id)}
            className={clsx(
              'px-3 py-1.5 rounded text-xs font-medium transition-colors',
              tab === t.id
                ? 'bg-nzt-accent text-nzt-bg'
                : 'bg-nzt-card text-nzt-muted hover:text-nzt-text border border-nzt-border'
            )}
          >
            {t.label}
          </button>
        ))}
      </div>

      {/* Overview Tab */}
      {tab === 'overview' && (
        <div className="space-y-3">
          {/* Ratio Cards */}
          <div className="grid grid-cols-5 gap-3">
            <Card title="SHARPE RATIO">
              <div className="text-[10px] text-nzt-muted mb-1" title="Risk-adjusted return: higher is better. >1.0 is good, >2.0 is excellent.">
                Return per unit of total volatility
              </div>
              <div className={clsx('text-3xl font-mono font-bold text-center py-2', (ratios?.sharpe || 0) >= 1.0 ? 'text-nzt-accent' : (ratios?.sharpe || 0) >= 0 ? 'text-yellow-400' : 'text-nzt-danger')}>
                {fmt(ratios?.sharpe || 0, 2)}
              </div>
            </Card>
            <Card title="SORTINO RATIO">
              <div className="text-[10px] text-nzt-muted mb-1" title="Like Sharpe but only penalises downside volatility. >1.5 is good.">
                Return per unit of downside volatility
              </div>
              <div className={clsx('text-3xl font-mono font-bold text-center py-2', (ratios?.sortino || 0) >= 1.5 ? 'text-nzt-accent' : 'text-yellow-400')}>
                {fmt(ratios?.sortino || 0, 2)}
              </div>
            </Card>
            <Card title="CALMAR RATIO">
              <div className="text-[10px] text-nzt-muted mb-1" title="Return divided by max drawdown. >1.0 means gains exceed worst drawdown.">
                Return per unit of max drawdown
              </div>
              <div className={clsx('text-3xl font-mono font-bold text-center py-2', (ratios?.calmar || 0) >= 1.0 ? 'text-nzt-accent' : 'text-yellow-400')}>
                {fmt(ratios?.calmar || 0, 2)}
              </div>
            </Card>
            <Card title="AVG R-MULTIPLE">
              <div className="text-[10px] text-nzt-muted mb-1" title="Average reward per unit of risk. >0.5R is profitable, >1.0R is excellent.">
                Average return per R risked
              </div>
              <div className={clsx('text-3xl font-mono font-bold text-center py-2', (ratios?.mean_r || 0) >= 0 ? 'text-nzt-accent' : 'text-nzt-danger')}>
                {fmt(ratios?.mean_r || 0, 2)}R
              </div>
            </Card>
            <Card title="TOTAL TRADES">
              <div className="text-[10px] text-nzt-muted mb-1">Total completed trades</div>
              <div className="text-3xl font-mono font-bold text-center py-2 text-blue-400">
                {ratios?.trade_count || 0}
              </div>
            </Card>
          </div>

          {/* Holdings */}
          <div className="grid grid-cols-3 gap-3">
            <Card title="EXPOSURE BY TICKER">
              <div className="text-[10px] text-nzt-muted mb-1" title="How much risk (in £) is allocated to each ticker right now.">
                Current risk allocation per ticker
              </div>
              {holdings?.by_ticker && Object.keys(holdings.by_ticker).length > 0 ? (
                <div className="space-y-1">
                  {Object.entries(holdings.by_ticker)
                    .sort(([, a]: any, [, b]: any) => b - a)
                    .map(([ticker, risk]: any) => (
                    <div key={ticker} className="flex items-center gap-2 text-xs">
                      <TickerLink ticker={ticker} className="text-xs w-16" />
                      <div className="flex-1 h-3 bg-nzt-bg rounded overflow-hidden">
                        <div className="h-full bg-nzt-accent/50 rounded"
                          style={{ width: `${(risk / (holdings.total_exposure || 1)) * 100}%` }} />
                      </div>
                      <span className="font-mono text-nzt-muted w-14 text-right">£{risk.toFixed(0)}</span>
                    </div>
                  ))}
                </div>
              ) : (
                <div className="text-center text-nzt-muted text-xs py-6">No open positions</div>
              )}
            </Card>

            <Card title="EXPOSURE BY STRATEGY">
              <div className="text-[10px] text-nzt-muted mb-1" title="Risk distributed across different trading strategies.">
                Strategy-level risk breakdown
              </div>
              {holdings?.by_strategy && Object.keys(holdings.by_strategy).length > 0 ? (
                <div className="space-y-1">
                  {Object.entries(holdings.by_strategy)
                    .sort(([, a]: any, [, b]: any) => b - a)
                    .map(([strategy, risk]: any) => (
                    <div key={strategy} className="flex items-center gap-2 text-xs">
                      <span className="text-nzt-accent w-20 truncate">{strategy}</span>
                      <div className="flex-1 h-3 bg-nzt-bg rounded overflow-hidden">
                        <div className="h-full bg-blue-500/50 rounded"
                          style={{ width: `${(risk / (holdings.total_exposure || 1)) * 100}%` }} />
                      </div>
                      <span className="font-mono text-nzt-muted w-14 text-right">£{risk.toFixed(0)}</span>
                    </div>
                  ))}
                </div>
              ) : (
                <div className="text-center text-nzt-muted text-xs py-6">No open positions</div>
              )}
            </Card>

            <Card title="LEAGUE TABLE SUMMARY">
              <div className="text-[10px] text-nzt-muted mb-1" title="A-Team = core tickers, B-Team = challengers competing for promotion.">
                Ticker universe rotation status
              </div>
              <div className="space-y-2 text-xs">
                <div className="flex justify-between">
                  <span className="text-nzt-accent font-bold">A-Team</span>
                  <span className="font-mono">{leagueTable?.a_team?.length || 12} tickers</span>
                </div>
                <div className="flex justify-between">
                  <span className="text-yellow-400 font-bold">B-Team</span>
                  <span className="font-mono">{leagueTable?.b_team?.length || 20} tickers</span>
                </div>
                <div className="flex justify-between">
                  <span className="text-nzt-muted font-bold">C-Team</span>
                  <span className="font-mono">{leagueTable?.c_team?.length || 0} tickers</span>
                </div>
                <div className="pt-1 border-t border-nzt-border flex justify-between">
                  <span className="text-nzt-muted">Total Tracked</span>
                  <span className="font-mono font-bold">{leagueTable?.total_tickers || 32}</span>
                </div>
              </div>
            </Card>
          </div>
        </div>
      )}

      {/* MFE/MAE Tab */}
      {tab === 'mfe-mae' && (
        <Card title="MFE / MAE SCATTER PLOT">
          <div className="text-[10px] text-nzt-muted mb-2" title="MFE = Maximum Favorable Excursion (best unrealised P&L during trade). MAE = Maximum Adverse Excursion (worst drawdown during trade). Ideal: high MFE, low MAE.">
            Each dot is a trade. X = MAE (how bad it got), Y = MFE (how good it got). Colour = win (green) / loss (red).
          </div>
          {mfeMae?.points && mfeMae.points.length > 0 ? (
            <div className="relative h-96 bg-nzt-bg rounded p-4">
              {/* Y-axis label */}
              <div className="absolute left-0 top-1/2 -translate-y-1/2 -rotate-90 text-[10px] text-nzt-muted">MFE (R)</div>
              {/* X-axis label */}
              <div className="absolute bottom-0 left-1/2 -translate-x-1/2 text-[10px] text-nzt-muted">MAE (R)</div>
              {/* Grid */}
              <div className="absolute inset-4 border border-nzt-border/30">
                {mfeMae.points.map((p: any, i: number) => {
                  const maxMfe = Math.max(...mfeMae.points.map((x: any) => x.mfe || 0), 3)
                  const maxMae = Math.max(...mfeMae.points.map((x: any) => x.mae || 0), 3)
                  const x = ((p.mae || 0) / maxMae) * 90
                  const y = 90 - ((p.mfe || 0) / maxMfe) * 90
                  return (
                    <div
                      key={i}
                      className={clsx(
                        'absolute w-2 h-2 rounded-full border',
                        (p.actual_r || 0) >= 0
                          ? 'bg-nzt-accent/60 border-nzt-accent'
                          : 'bg-nzt-danger/60 border-nzt-danger'
                      )}
                      style={{ left: `${x + 5}%`, top: `${y + 5}%` }}
                      title={`${p.ticker} | MFE: ${p.mfe}R | MAE: ${p.mae}R | Result: ${p.actual_r}R | ${p.exit_reason}`}
                    />
                  )
                })}
              </div>
            </div>
          ) : (
            <div className="text-center text-nzt-muted text-xs py-12">Need completed trades for MFE/MAE analysis</div>
          )}
          {/* Trade list below */}
          {mfeMae?.points && mfeMae.points.length > 0 && (
            <div className="mt-3 overflow-x-auto max-h-48">
              <table className="w-full text-xs">
                <thead>
                  <tr className="border-b border-nzt-border text-nzt-muted">
                    <th className="py-1 px-2 text-left">Ticker</th>
                    <th className="py-1 px-2 text-left">Strategy</th>
                    <th className="py-1 px-2 text-right">MFE</th>
                    <th className="py-1 px-2 text-right">MAE</th>
                    <th className="py-1 px-2 text-right">Actual R</th>
                    <th className="py-1 px-2 text-right">P&L</th>
                    <th className="py-1 px-2 text-left">Exit</th>
                  </tr>
                </thead>
                <tbody>
                  {mfeMae.points.slice(0, 30).map((p: any, i: number) => (
                    <tr key={i} className="border-b border-nzt-border/20">
                      <td className="py-1 px-2"><TickerLink ticker={p.ticker} className="text-xs" /></td>
                      <td className="py-1 px-2 text-nzt-accent">{p.strategy}</td>
                      <td className="py-1 px-2 text-right font-mono text-nzt-accent">{fmt(p.mfe, 2)}R</td>
                      <td className="py-1 px-2 text-right font-mono text-nzt-danger">{fmt(p.mae, 2)}R</td>
                      <td className={clsx('py-1 px-2 text-right font-mono', p.actual_r >= 0 ? 'text-nzt-accent' : 'text-nzt-danger')}>
                        {fmt(p.actual_r, 2)}R
                      </td>
                      <td className={clsx('py-1 px-2 text-right font-mono', p.pnl >= 0 ? 'text-nzt-accent' : 'text-nzt-danger')}>
                        £{p.pnl?.toFixed(2)}
                      </td>
                      <td className="py-1 px-2 text-nzt-muted">{p.exit_reason}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </Card>
      )}

      {/* R-Distribution Tab */}
      {tab === 'distribution' && (
        <Card title="R-MULTIPLE DISTRIBUTION">
          <div className="text-[10px] text-nzt-muted mb-2" title="Shows how many trades fell in each R-multiple bucket. A right-skewed distribution (more green bars on the right) = profitable system.">
            Histogram of trade outcomes measured in R-multiples. Green = profitable, Red = losing. Right-skew = edge.
          </div>
          {distribution?.buckets && distribution.buckets.length > 0 ? (
            <>
              <div className="h-48 flex items-end gap-0.5 px-2">
                {distribution.buckets.map((b: any, i: number) => {
                  const maxCount = Math.max(...distribution.buckets.map((x: any) => x.count || 0), 1)
                  const h = (b.count / maxCount) * 100
                  return (
                    <div key={i} className="flex-1 flex flex-col items-center justify-end">
                      <div className="text-[8px] text-nzt-muted mb-0.5">{b.count || 0}</div>
                      <div
                        className={clsx('w-full rounded-t', b.low >= 0 ? 'bg-nzt-accent/70' : 'bg-nzt-danger/70')}
                        style={{ height: `${Math.max(2, h)}%` }}
                        title={`${b.range}: ${b.count} trades`}
                      />
                      <div className="text-[7px] text-nzt-muted mt-0.5 rotate-45 origin-left">{b.low}R</div>
                    </div>
                  )
                })}
              </div>
              <div className="flex justify-between text-xs text-nzt-muted mt-2 pt-2 border-t border-nzt-border">
                <span>Total: {distribution.total} trades</span>
                <span>Mean: {fmt(distribution.mean_r || 0, 3)}R</span>
              </div>
            </>
          ) : (
            <div className="text-center text-nzt-muted text-xs py-12">Need completed trades for distribution analysis</div>
          )}
        </Card>
      )}

      {/* Compounding Tab */}
      {tab === 'compounding' && (
        <div className="space-y-3">
          <Card title="2% DAILY COMPOUNDING — AUDIT TRAIL">
            <div className="text-[10px] text-nzt-muted mb-2" title="Day-by-day comparison of actual P&L vs the 2% daily compound target. The gap column shows if we're ahead or behind schedule.">
              £10,000 x (1.02)^252 = £1,485,757. Each day must contribute 2% growth on current equity to stay on track.
            </div>
            {compounding?.days && compounding.days.length > 0 ? (
              <>
                <div className="grid grid-cols-4 gap-3 mb-3">
                  <div className="bg-nzt-bg rounded p-3 text-center">
                    <div className="text-[10px] text-nzt-muted">Current Equity</div>
                    <div className="text-2xl font-mono font-bold text-nzt-accent">£{(compounding.current_equity || 10000).toLocaleString()}</div>
                  </div>
                  <div className="bg-nzt-bg rounded p-3 text-center">
                    <div className="text-[10px] text-nzt-muted">Target Equity</div>
                    <div className="text-2xl font-mono font-bold">£{(compounding.target_equity || 10000).toLocaleString()}</div>
                  </div>
                  <div className="bg-nzt-bg rounded p-3 text-center">
                    <div className="text-[10px] text-nzt-muted">Trading Day</div>
                    <div className="text-2xl font-mono font-bold text-blue-400">#{compounding.trading_day || 0} / 252</div>
                  </div>
                  <div className="bg-nzt-bg rounded p-3 text-center">
                    <div className="text-[10px] text-nzt-muted">Status</div>
                    <div className={clsx('text-2xl font-bold', compounding.on_track ? 'text-nzt-accent' : 'text-nzt-danger')}>
                      {compounding.on_track ? 'ON TRACK' : 'BEHIND'}
                    </div>
                  </div>
                </div>

                {/* Day-by-day table */}
                <div className="overflow-x-auto max-h-72">
                  <table className="w-full text-xs">
                    <thead className="sticky top-0 bg-nzt-card">
                      <tr className="border-b border-nzt-border text-nzt-muted">
                        <th className="py-1 px-2 text-left">Day #</th>
                        <th className="py-1 px-2 text-left">Date</th>
                        <th className="py-1 px-2 text-right">Day P&L</th>
                        <th className="py-1 px-2 text-right">Equity</th>
                        <th className="py-1 px-2 text-right">Target</th>
                        <th className="py-1 px-2 text-right">Gap</th>
                      </tr>
                    </thead>
                    <tbody>
                      {compounding.days.map((d: any, i: number) => (
                        <tr key={i} className="border-b border-nzt-border/20">
                          <td className="py-1 px-2 font-mono text-blue-400">#{d.trading_day}</td>
                          <td className="py-1 px-2 text-nzt-muted">{d.date}</td>
                          <td className={clsx('py-1 px-2 text-right font-mono', (d.day_pnl || 0) >= 0 ? 'text-nzt-accent' : 'text-nzt-danger')}>
                            £{(d.day_pnl || 0).toFixed(2)}
                          </td>
                          <td className="py-1 px-2 text-right font-mono">£{(d.equity || 0).toFixed(0)}</td>
                          <td className="py-1 px-2 text-right font-mono text-nzt-muted">£{(d.target || 0).toFixed(0)}</td>
                          <td className={clsx('py-1 px-2 text-right font-mono font-bold', (d.gap || 0) >= 0 ? 'text-nzt-accent' : 'text-nzt-danger')}>
                            {(d.gap || 0) >= 0 ? '+' : ''}£{(d.gap || 0).toFixed(0)}
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              </>
            ) : (
              <div className="text-center text-nzt-muted text-xs py-12">
                No trades yet. The compounding audit trail begins with the first trade close.
              </div>
            )}
          </Card>
        </div>
      )}

      {/* Monte Carlo Tab */}
      {tab === 'monte-carlo' && (
        <div className="space-y-3">
          <Card title="MONTE CARLO SIMULATION">
            <div className="text-[10px] text-nzt-muted mb-3">
              1,000 simulations of 252 random trades sampled from your actual R-distribution. Shows probability bands of equity outcomes over one trading year.
            </div>
            {monteCarloResults ? (
              <>
                {/* Key Metrics */}
                <div className="grid grid-cols-5 gap-3 mb-4">
                  <div className="bg-nzt-bg rounded p-3 text-center">
                    <div className="text-[10px] text-nzt-muted">P(Doubling)</div>
                    <div className={clsx('text-2xl font-mono font-bold', monteCarloResults.probDoubling >= 50 ? 'text-nzt-accent' : monteCarloResults.probDoubling >= 20 ? 'text-yellow-400' : 'text-nzt-danger')}>
                      {fmt(monteCarloResults.probDoubling, 1)}%
                    </div>
                    <div className="text-[9px] text-nzt-muted">in 1 year</div>
                  </div>
                  <div className="bg-nzt-bg rounded p-3 text-center">
                    <div className="text-[10px] text-nzt-muted">Median Outcome</div>
                    <div className={clsx('text-2xl font-mono font-bold', monteCarloResults.median >= 10000 ? 'text-nzt-accent' : 'text-nzt-danger')}>
                      {fmtDollar(monteCarloResults.median)}
                    </div>
                    <div className="text-[9px] text-nzt-muted">50th percentile</div>
                  </div>
                  <div className="bg-nzt-bg rounded p-3 text-center">
                    <div className="text-[10px] text-nzt-muted">10th Percentile</div>
                    <div className="text-2xl font-mono font-bold text-red-400">
                      {fmtDollar(monteCarloResults.p10Final)}
                    </div>
                    <div className="text-[9px] text-nzt-muted">worst 10%</div>
                  </div>
                  <div className="bg-nzt-bg rounded p-3 text-center">
                    <div className="text-[10px] text-nzt-muted">90th Percentile</div>
                    <div className="text-2xl font-mono font-bold text-green-400">
                      {fmtDollar(monteCarloResults.p90Final)}
                    </div>
                    <div className="text-[9px] text-nzt-muted">best 10%</div>
                  </div>
                  <div className="bg-nzt-bg rounded p-3 text-center">
                    <div className="text-[10px] text-nzt-muted">P(Ruin &lt;50%)</div>
                    <div className={clsx('text-2xl font-mono font-bold', monteCarloResults.probRuin <= 5 ? 'text-nzt-accent' : monteCarloResults.probRuin <= 20 ? 'text-yellow-400' : 'text-nzt-danger')}>
                      {fmt(monteCarloResults.probRuin, 1)}%
                    </div>
                    <div className="text-[9px] text-nzt-muted">losing half</div>
                  </div>
                </div>

                {/* Percentile Band Visualization */}
                <div className="relative h-64 bg-nzt-bg rounded-lg p-4 overflow-hidden">
                  {/* Y-axis labels */}
                  <div className="absolute left-1 top-4 text-[9px] text-nzt-muted font-mono">
                    {fmtDollar(Math.max(...monteCarloResults.bands.map(b => b.p90)))}
                  </div>
                  <div className="absolute left-1 bottom-4 text-[9px] text-nzt-muted font-mono">
                    {fmtDollar(Math.min(...monteCarloResults.bands.map(b => b.p10)))}
                  </div>

                  {/* Band chart using stacked bars for percentile ranges */}
                  <div className="absolute inset-0 px-12 py-6 flex items-end gap-px">
                    {monteCarloResults.bands.map((b, i) => {
                      const allMin = Math.min(...monteCarloResults.bands.map(x => x.p10))
                      const allMax = Math.max(...monteCarloResults.bands.map(x => x.p90))
                      const range = allMax - allMin || 1
                      const chartHeight = 100

                      // Calculate heights for each band section (as % of total height)
                      const p10Offset = ((b.p10 - allMin) / range) * chartHeight
                      const p25Height = ((b.p25 - b.p10) / range) * chartHeight
                      const p50Height = ((b.p50 - b.p25) / range) * chartHeight
                      const p75Height = ((b.p75 - b.p50) / range) * chartHeight
                      const p90Height = ((b.p90 - b.p75) / range) * chartHeight

                      return (
                        <div
                          key={i}
                          className="flex-1 flex flex-col justify-end relative"
                          title={`Trade ${b.trade}: P10=${fmtDollar(b.p10)} P25=${fmtDollar(b.p25)} P50=${fmtDollar(b.p50)} P75=${fmtDollar(b.p75)} P90=${fmtDollar(b.p90)}`}
                        >
                          {/* p10-p25 band (worst outcomes) */}
                          <div style={{ marginBottom: `${p10Offset}%` }} className="flex flex-col">
                            <div className="bg-red-500/15 min-w-[2px]" style={{ height: `${Math.max(p25Height, 0.5)}px` }} />
                            <div className="bg-orange-500/20 min-w-[2px]" style={{ height: `${Math.max(p50Height, 0.5)}px` }} />
                            <div className="bg-green-500/20 min-w-[2px]" style={{ height: `${Math.max(p75Height, 0.5)}px` }} />
                            <div className="bg-green-500/10 min-w-[2px]" style={{ height: `${Math.max(p90Height, 0.5)}px` }} />
                          </div>
                        </div>
                      )
                    })}
                  </div>

                  {/* Starting equity reference line */}
                  {(() => {
                    const allMin = Math.min(...monteCarloResults.bands.map(x => x.p10))
                    const allMax = Math.max(...monteCarloResults.bands.map(x => x.p90))
                    const range = allMax - allMin || 1
                    const startLinePct = ((10000 - allMin) / range) * 100
                    return (
                      <div
                        className="absolute left-12 right-4 border-t border-dashed border-nzt-muted/40"
                        style={{ bottom: `${Math.min(Math.max(startLinePct, 5), 95)}%` }}
                      >
                        <span className="absolute right-0 -top-3 text-[9px] text-nzt-muted">$10K start</span>
                      </div>
                    )
                  })()}
                </div>

                {/* X-axis */}
                <div className="flex justify-between text-[9px] text-nzt-muted mt-1 px-12">
                  <span>Trade 0</span>
                  <span>Trade 63</span>
                  <span>Trade 126</span>
                  <span>Trade 189</span>
                  <span>Trade 252</span>
                </div>

                {/* Legend */}
                <div className="flex gap-4 mt-3 text-[10px] justify-center">
                  <div className="flex items-center gap-1">
                    <div className="w-3 h-3 rounded bg-red-500/15 border border-red-500/30" />
                    <span className="text-nzt-muted">10-25th %ile</span>
                  </div>
                  <div className="flex items-center gap-1">
                    <div className="w-3 h-3 rounded bg-orange-500/20 border border-orange-500/30" />
                    <span className="text-nzt-muted">25-50th %ile</span>
                  </div>
                  <div className="flex items-center gap-1">
                    <div className="w-3 h-3 rounded bg-green-500/20 border border-green-500/30" />
                    <span className="text-nzt-muted">50-75th %ile</span>
                  </div>
                  <div className="flex items-center gap-1">
                    <div className="w-3 h-3 rounded bg-green-500/10 border border-green-500/30" />
                    <span className="text-nzt-muted">75-90th %ile</span>
                  </div>
                </div>

                {/* Simulation info */}
                <div className="flex justify-between text-[10px] text-nzt-muted mt-3 pt-2 border-t border-nzt-border">
                  <span>Sample size: {monteCarloResults.sampleSize} trades</span>
                  <span>Simulations: {monteCarloResults.simulations.toLocaleString()}</span>
                  <span>Trades/year: {monteCarloResults.tradesPerYear}</span>
                  <span>Risk/trade: 1%</span>
                </div>
              </>
            ) : (
              <div className="text-center text-nzt-muted text-xs py-12">
                Need at least 5 completed trades to run Monte Carlo simulation.
                <br />
                <span className="text-[10px]">The simulation samples from your actual R-multiple distribution to project future equity paths.</span>
              </div>
            )}
          </Card>
        </div>
      )}
    </div>
  )
}
