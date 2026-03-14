'use client'

import { useState, useEffect, useCallback } from 'react'
import clsx from 'clsx'
import { Card, NavTabs, TickerLink, SectorSignalBadge, ScoreBar } from '../lib/components'
import { fetchAPI, fmt, fmtPct, timeAgo } from '../lib/api'

// === Types ===
type Tab = 'heatmap' | 'rotation' | 'flows' | 'instruments' | 'regime-matrix'

interface SectorRanking {
  sector: string
  rank: number
  composite_score: number
  momentum_score: number
  momentum_acceleration: number
  volatility_expansion_score: number
  capital_inflow_score: number
  relative_strength_vs_benchmark: number
  leadership_status: string
  instruments: string[]
  best_instrument: string
  best_instrument_score: number
  trend_direction: string
  rotation_signal: string
}

interface RotationSnapshot {
  generated_at: string
  rankings: SectorRanking[]
  current_leader: string
  transition_alert: {
    detected_at: string
    old_leader: string
    new_leader: string
    confidence: number
    signal_type: string
    instruments_to_watch: string[]
    actionable_insight: string
  } | null
  market_risk_mode: string
  macro_regime: string
  key_insight: string
}

interface SectorFallback {
  sectors: Array<{
    sector: string
    composite_score: number
    rotation_signal: string
    leadership_status: string
    instruments: string[]
    best_instrument: string
  }>
  inflows: string[]
  leaders: string[]
}

interface RegimeData {
  current: { regime: string; timestamp: string } | null
  history: Array<{ regime: string; timestamp: string }>
}

// === Helpers ===

function sectorDisplayName(raw: string): string {
  return raw.replace(/_/g, ' ')
}

/** Map composite score 0-100 to a heatmap colour class */
function heatColor(score: number): string {
  if (score >= 75) return 'bg-green-600/80 text-white'
  if (score >= 60) return 'bg-green-800/60 text-green-200'
  if (score >= 50) return 'bg-yellow-700/50 text-yellow-200'
  if (score >= 35) return 'bg-orange-800/50 text-orange-200'
  return 'bg-red-900/60 text-red-300'
}

/** Map leadership to a ring colour */
function leadershipRing(status: string): string {
  if (status === 'LEADER') return 'ring-2 ring-green-400'
  if (status === 'RISING') return 'ring-2 ring-amber-400'
  if (status === 'FADING') return 'ring-2 ring-orange-500'
  if (status === 'LAGGARD') return 'ring-2 ring-red-500'
  return 'ring-1 ring-nzt-border'
}

/** Classify sector into business cycle phase for Rotation Radar */
function cyclePhase(sector: string, momAccel: number, leadership: string): string {
  // Early cycle: sectors accelerating from a low base
  if (momAccel > 3 && (leadership === 'RISING' || leadership === 'NEUTRAL')) return 'EARLY'
  // Mid cycle: leaders with strong momentum
  if (leadership === 'LEADER' && momAccel >= 0) return 'MID'
  // Late cycle: leaders with decelerating momentum
  if (leadership === 'LEADER' && momAccel < 0) return 'LATE'
  if (leadership === 'FADING') return 'LATE'
  if (leadership === 'LAGGARD') return 'LATE'
  if (momAccel > 1) return 'EARLY'
  return 'MID'
}

const CYCLE_COLORS: Record<string, { bg: string; text: string; label: string }> = {
  EARLY: { bg: 'bg-blue-900/50', text: 'text-blue-400', label: 'Early Cycle' },
  MID:   { bg: 'bg-green-900/50', text: 'text-green-400', label: 'Mid Cycle' },
  LATE:  { bg: 'bg-amber-900/50', text: 'text-amber-400', label: 'Late Cycle' },
}

/** Regime names for the performance matrix */
const REGIME_NAMES = ['RISK_ON', 'RISK_OFF', 'NEUTRAL'] as const

/** Which sectors typically outperform in each regime (structural knowledge) */
const REGIME_SECTOR_AFFINITY: Record<string, string[]> = {
  RISK_ON:  ['AI_TECH', 'SEMICONDUCTORS', 'BROAD_US_LONG', 'CRYPTO_TECH', 'EV_TECH'],
  RISK_OFF: ['BROAD_US_SHORT', 'COMMODITIES', 'HEALTHCARE'],
  NEUTRAL:  ['FINANCIALS', 'EU_MARKETS', 'ENERGY'],
}

export default function SectorsPage() {
  const [tab, setTab] = useState<Tab>('heatmap')
  const [loading, setLoading] = useState(true)

  // Data states
  const [snapshot, setSnapshot] = useState<RotationSnapshot | null>(null)
  const [sectorFallback, setSectorFallback] = useState<SectorFallback | null>(null)
  const [regime, setRegime] = useState<RegimeData | null>(null)

  const refresh = useCallback(async () => {
    setLoading(true)
    try {
      const results = await Promise.allSettled([
        fetchAPI<any>('/api/sectors/heatmap'),
        fetchAPI<any>('/api/sector-rotation'),
        fetchAPI<any>('/api/regime'),
      ])
      const get = (i: number) => results[i].status === 'fulfilled' ? (results[i] as any).value : null

      setSnapshot(get(0))
      setSectorFallback(get(1))
      setRegime(get(2))
    } catch {}
    setLoading(false)
  }, [])

  useEffect(() => {
    refresh()
    const timer = setInterval(refresh, 30000)
    return () => clearInterval(timer)
  }, [refresh])

  // Unified sector list: prefer snapshot.rankings, fall back to sectorFallback.sectors
  const sectors: SectorRanking[] = (snapshot?.rankings && snapshot.rankings.length > 0)
    ? snapshot.rankings
    : (sectorFallback?.sectors || []).map((s, i) => ({
        sector: s.sector,
        rank: i + 1,
        composite_score: s.composite_score || 0,
        momentum_score: 50,
        momentum_acceleration: 0,
        volatility_expansion_score: 50,
        capital_inflow_score: 50,
        relative_strength_vs_benchmark: 50,
        leadership_status: s.leadership_status || 'NEUTRAL',
        instruments: s.instruments || [],
        best_instrument: s.best_instrument || '',
        best_instrument_score: 0,
        trend_direction: 'SIDEWAYS',
        rotation_signal: s.rotation_signal || 'NEUTRAL',
      }))

  const currentRegime = regime?.current?.regime || snapshot?.market_risk_mode || 'UNKNOWN'
  const macroRegime = snapshot?.macro_regime || 'UNKNOWN'
  const currentLeader = snapshot?.current_leader || (sectors.length > 0 ? sectors[0].sector : 'UNKNOWN')
  const transitionAlert = snapshot?.transition_alert || null
  const keyInsight = snapshot?.key_insight || ''

  const tabs = [
    { id: 'heatmap' as Tab, label: 'Sector Heatmap' },
    { id: 'rotation' as Tab, label: 'Rotation Radar' },
    { id: 'flows' as Tab, label: 'Money Flows' },
    { id: 'instruments' as Tab, label: 'Best Instruments' },
    { id: 'regime-matrix' as Tab, label: 'Regime Matrix' },
  ]

  return (
    <div className="min-h-screen p-4 max-w-[1920px] mx-auto">
      <header className="flex items-center justify-between mb-4">
        <div className="flex items-center gap-4">
          <h1 className="text-2xl font-bold tracking-tight">
            NZT-48 <span className="text-nzt-accent">Sector Intelligence</span>
          </h1>
          {loading && <span className="text-[10px] text-nzt-muted animate-pulse">Refreshing...</span>}
        </div>
        <NavTabs />
      </header>

      {/* Key insight banner */}
      {keyInsight && (
        <div className="mb-3 px-3 py-2 bg-nzt-card rounded-lg border border-nzt-border text-xs text-nzt-text">
          <span className="text-nzt-accent font-bold mr-2">INSIGHT:</span>
          {keyInsight}
        </div>
      )}

      {/* Transition alert */}
      {transitionAlert && (
        <div className="mb-3 px-3 py-2 bg-amber-900/20 rounded-lg border border-amber-500/50 text-xs">
          <span className="text-amber-400 font-bold mr-2">ROTATION DETECTED:</span>
          <span className="text-nzt-text">
            {sectorDisplayName(transitionAlert.old_leader)} &rarr; {sectorDisplayName(transitionAlert.new_leader)}
            <span className="text-nzt-muted ml-2">
              (confidence {fmt(transitionAlert.confidence * 100, 0)}% | {transitionAlert.signal_type})
            </span>
          </span>
        </div>
      )}

      {/* Top-level metric cards */}
      <div className="grid grid-cols-4 gap-3 mb-4">
        <Card title="CURRENT LEADER">
          <div className="text-xl font-bold text-nzt-accent text-center py-1">
            {sectorDisplayName(currentLeader)}
          </div>
        </Card>
        <Card title="MARKET MODE">
          <div className={clsx('text-xl font-bold text-center py-1',
            currentRegime === 'RISK_ON' ? 'text-nzt-accent' :
            currentRegime === 'RISK_OFF' ? 'text-nzt-danger' : 'text-yellow-400'
          )}>
            {currentRegime.replace(/_/g, ' ')}
          </div>
        </Card>
        <Card title="MACRO REGIME">
          <div className={clsx('text-xl font-bold text-center py-1',
            macroRegime === 'EXPANSION' ? 'text-nzt-accent' :
            macroRegime === 'CONTRACTION' ? 'text-nzt-danger' : 'text-yellow-400'
          )}>
            {macroRegime}
          </div>
        </Card>
        <Card title="SECTORS TRACKED">
          <div className="text-xl font-mono font-bold text-center py-1 text-blue-400">
            {sectors.length}
          </div>
        </Card>
      </div>

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

      {/* ========== HEATMAP TAB ========== */}
      {tab === 'heatmap' && (
        <div className="space-y-3">
          <Card title="SECTOR RELATIVE STRENGTH HEATMAP">
            <div className="text-[10px] text-nzt-muted mb-3" title="Each tile represents a sector. Size reflects composite score. Colour indicates relative strength: green = strong, red = weak.">
              Colour by composite score (momentum + volatility + inflows + RS). Ring = leadership status.
            </div>
            {sectors.length > 0 ? (
              <>
                {/* Heatmap grid */}
                <div className="grid grid-cols-4 gap-2">
                  {sectors.map((s) => (
                    <div
                      key={s.sector}
                      className={clsx(
                        'rounded-lg p-3 transition-all hover:scale-[1.02]',
                        heatColor(s.composite_score),
                        leadershipRing(s.leadership_status),
                      )}
                      title={`${sectorDisplayName(s.sector)}\nComposite: ${s.composite_score}\nMomentum: ${s.momentum_score}\nVol Expansion: ${s.volatility_expansion_score}\nInflow: ${s.capital_inflow_score}\nRS: ${s.relative_strength_vs_benchmark}`}
                    >
                      <div className="flex items-center justify-between mb-1">
                        <span className="text-[11px] font-bold truncate">{sectorDisplayName(s.sector)}</span>
                        <span className="text-[10px] font-mono">#{s.rank}</span>
                      </div>
                      <div className="text-2xl font-mono font-bold text-center py-1">
                        {fmt(s.composite_score, 0)}
                      </div>
                      <div className="flex items-center justify-between mt-1">
                        <SectorSignalBadge signal={s.rotation_signal} />
                        <span className="text-[9px] font-mono">
                          {s.trend_direction === 'UP' ? '\u25B2' : s.trend_direction === 'DOWN' ? '\u25BC' : '\u25C6'}{' '}
                          {s.momentum_acceleration >= 0 ? '+' : ''}{fmt(s.momentum_acceleration, 1)}
                        </span>
                      </div>
                    </div>
                  ))}
                </div>

                {/* Detailed breakdown table */}
                <div className="mt-4 overflow-x-auto">
                  <table className="w-full text-xs">
                    <thead>
                      <tr className="border-b border-nzt-border text-nzt-muted">
                        <th className="py-1 px-2 text-left">#</th>
                        <th className="py-1 px-2 text-left">Sector</th>
                        <th className="py-1 px-2 text-right">Composite</th>
                        <th className="py-1 px-2 text-right">Momentum</th>
                        <th className="py-1 px-2 text-right">Accel</th>
                        <th className="py-1 px-2 text-right">Vol Exp</th>
                        <th className="py-1 px-2 text-right">Inflow</th>
                        <th className="py-1 px-2 text-right">RS</th>
                        <th className="py-1 px-2 text-center">Trend</th>
                        <th className="py-1 px-2 text-center">Signal</th>
                        <th className="py-1 px-2 text-center">Status</th>
                      </tr>
                    </thead>
                    <tbody>
                      {sectors.map((s) => (
                        <tr key={s.sector} className="border-b border-nzt-border/20 hover:bg-nzt-bg/50">
                          <td className="py-1.5 px-2 font-mono text-blue-400">#{s.rank}</td>
                          <td className="py-1.5 px-2 font-bold text-nzt-text">{sectorDisplayName(s.sector)}</td>
                          <td className={clsx('py-1.5 px-2 text-right font-mono font-bold',
                            s.composite_score >= 60 ? 'text-nzt-accent' : s.composite_score >= 40 ? 'text-yellow-400' : 'text-nzt-danger'
                          )}>
                            {fmt(s.composite_score, 1)}
                          </td>
                          <td className="py-1.5 px-2 text-right font-mono text-nzt-text">{fmt(s.momentum_score, 1)}</td>
                          <td className={clsx('py-1.5 px-2 text-right font-mono',
                            s.momentum_acceleration >= 0 ? 'text-nzt-accent' : 'text-nzt-danger'
                          )}>
                            {s.momentum_acceleration >= 0 ? '+' : ''}{fmt(s.momentum_acceleration, 2)}
                          </td>
                          <td className="py-1.5 px-2 text-right font-mono text-nzt-text">{fmt(s.volatility_expansion_score, 1)}</td>
                          <td className={clsx('py-1.5 px-2 text-right font-mono',
                            s.capital_inflow_score >= 60 ? 'text-nzt-accent' : s.capital_inflow_score < 35 ? 'text-nzt-danger' : 'text-nzt-text'
                          )}>
                            {fmt(s.capital_inflow_score, 1)}
                          </td>
                          <td className="py-1.5 px-2 text-right font-mono text-nzt-text">{fmt(s.relative_strength_vs_benchmark, 1)}</td>
                          <td className="py-1.5 px-2 text-center text-xs">
                            {s.trend_direction === 'UP' ? <span className="text-nzt-accent">{'\u25B2'} UP</span>
                              : s.trend_direction === 'DOWN' ? <span className="text-nzt-danger">{'\u25BC'} DOWN</span>
                              : <span className="text-nzt-muted">{'\u25C6'} FLAT</span>}
                          </td>
                          <td className="py-1.5 px-2 text-center"><SectorSignalBadge signal={s.rotation_signal} /></td>
                          <td className="py-1.5 px-2 text-center"><SectorSignalBadge signal={s.leadership_status} /></td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              </>
            ) : (
              <div className="text-center text-nzt-muted text-xs py-12">No sector data available yet. Waiting for sector rotation scan.</div>
            )}
          </Card>
        </div>
      )}

      {/* ========== ROTATION RADAR TAB ========== */}
      {tab === 'rotation' && (
        <div className="space-y-3">
          <Card title="SECTOR ROTATION RADAR">
            <div className="text-[10px] text-nzt-muted mb-3" title="Sectors classified by business cycle position. Early cycle = accelerating from low base. Mid cycle = established leaders. Late cycle = decelerating leaders.">
              Sectors positioned by business cycle phase. Momentum acceleration determines phase classification.
            </div>
            {sectors.length > 0 ? (
              <>
                {/* Cycle phase lanes */}
                <div className="grid grid-cols-3 gap-3 mb-4">
                  {(['EARLY', 'MID', 'LATE'] as const).map(phase => {
                    const phaseSectors = sectors.filter(s =>
                      cyclePhase(s.sector, s.momentum_acceleration, s.leadership_status) === phase
                    )
                    const cfg = CYCLE_COLORS[phase]
                    return (
                      <div key={phase} className={clsx('rounded-lg border border-nzt-border p-3', cfg.bg)}>
                        <div className={clsx('text-xs font-bold mb-2', cfg.text)}>{cfg.label}</div>
                        {phaseSectors.length > 0 ? (
                          <div className="space-y-2">
                            {phaseSectors.map(s => (
                              <div key={s.sector} className="bg-nzt-bg/50 rounded p-2">
                                <div className="flex items-center justify-between mb-1">
                                  <span className="text-[11px] font-bold text-nzt-text">{sectorDisplayName(s.sector)}</span>
                                  <span className="text-[10px] font-mono text-nzt-muted">
                                    {s.momentum_acceleration >= 0 ? '+' : ''}{fmt(s.momentum_acceleration, 1)} accel
                                  </span>
                                </div>
                                <ScoreBar label="Composite" value={s.composite_score} />
                                <ScoreBar label="Momentum" value={s.momentum_score} />
                                <div className="flex items-center justify-between mt-1">
                                  <SectorSignalBadge signal={s.leadership_status} />
                                  <SectorSignalBadge signal={s.rotation_signal} />
                                </div>
                              </div>
                            ))}
                          </div>
                        ) : (
                          <div className="text-center text-nzt-muted text-[10px] py-4">No sectors in this phase</div>
                        )}
                      </div>
                    )
                  })}
                </div>

                {/* Rotation flow arrows */}
                <div className="bg-nzt-bg rounded-lg border border-nzt-border p-3">
                  <div className="text-[10px] text-nzt-muted mb-2 font-semibold uppercase tracking-wider">Capital Flow Direction</div>
                  <div className="flex items-center gap-2 flex-wrap">
                    {sectors
                      .filter(s => s.rotation_signal === 'INFLOW')
                      .map(s => (
                        <span key={s.sector} className="px-2 py-1 bg-green-900/40 text-green-400 rounded text-[11px] font-bold">
                          {'\u2191'} {sectorDisplayName(s.sector)}
                        </span>
                      ))
                    }
                    {sectors
                      .filter(s => s.rotation_signal === 'OUTFLOW')
                      .map(s => (
                        <span key={s.sector} className="px-2 py-1 bg-red-900/40 text-red-400 rounded text-[11px] font-bold">
                          {'\u2193'} {sectorDisplayName(s.sector)}
                        </span>
                      ))
                    }
                    {sectors.filter(s => s.rotation_signal === 'INFLOW').length === 0 &&
                     sectors.filter(s => s.rotation_signal === 'OUTFLOW').length === 0 && (
                      <span className="text-nzt-muted text-[11px]">No strong rotation signals detected</span>
                    )}
                  </div>
                </div>
              </>
            ) : (
              <div className="text-center text-nzt-muted text-xs py-12">No rotation data available yet.</div>
            )}
          </Card>
        </div>
      )}

      {/* ========== MONEY FLOWS TAB ========== */}
      {tab === 'flows' && (
        <div className="space-y-3">
          <Card title="SECTOR MONEY FLOW (CAPITAL INFLOW VELOCITY)">
            <div className="text-[10px] text-nzt-muted mb-3" title="Capital inflow score based on relative volume (RVOL) across all instruments in each sector. Higher RVOL = more capital flowing into the sector.">
              RVOL surge across sector instruments. Bars show capital inflow velocity score (0-100). Green = strong inflow, Red = outflow.
            </div>
            {sectors.length > 0 ? (
              <>
                {/* Horizontal flow bars */}
                <div className="space-y-2">
                  {[...sectors]
                    .sort((a, b) => b.capital_inflow_score - a.capital_inflow_score)
                    .map(s => {
                      const score = s.capital_inflow_score
                      const isInflow = score >= 60
                      const isOutflow = score < 35
                      return (
                        <div key={s.sector} className="group">
                          <div className="flex items-center gap-2">
                            <span className="text-[11px] font-bold text-nzt-text w-32 shrink-0 truncate">
                              {sectorDisplayName(s.sector)}
                            </span>
                            <div className="flex-1 h-5 bg-nzt-bg rounded-full overflow-hidden relative">
                              <div
                                className={clsx(
                                  'h-full rounded-full transition-all',
                                  isInflow ? 'bg-green-500/70' : isOutflow ? 'bg-red-500/70' : 'bg-yellow-500/50'
                                )}
                                style={{ width: `${Math.max(3, score)}%` }}
                              />
                              <span className="absolute inset-0 flex items-center justify-center text-[10px] font-mono font-bold text-nzt-text">
                                {fmt(score, 0)}
                              </span>
                            </div>
                            <SectorSignalBadge signal={s.rotation_signal} />
                            <span className={clsx('text-[10px] font-mono w-16 text-right',
                              s.momentum_acceleration >= 0 ? 'text-nzt-accent' : 'text-nzt-danger'
                            )}>
                              {s.momentum_acceleration >= 0 ? '+' : ''}{fmt(s.momentum_acceleration, 1)}
                            </span>
                          </div>
                        </div>
                      )
                    })
                  }
                </div>

                {/* Flow summary */}
                <div className="grid grid-cols-3 gap-3 mt-4">
                  <div className="bg-green-900/20 rounded-lg border border-green-500/30 p-3">
                    <div className="text-[10px] text-green-400 font-bold mb-1">INFLOW SECTORS</div>
                    <div className="space-y-1">
                      {sectors.filter(s => s.rotation_signal === 'INFLOW').length > 0 ? (
                        sectors.filter(s => s.rotation_signal === 'INFLOW').map(s => (
                          <div key={s.sector} className="text-xs text-nzt-text flex justify-between">
                            <span>{sectorDisplayName(s.sector)}</span>
                            <span className="font-mono text-green-400">{fmt(s.capital_inflow_score, 0)}</span>
                          </div>
                        ))
                      ) : (
                        <div className="text-[10px] text-nzt-muted">None</div>
                      )}
                    </div>
                  </div>
                  <div className="bg-nzt-bg rounded-lg border border-nzt-border p-3">
                    <div className="text-[10px] text-yellow-400 font-bold mb-1">NEUTRAL SECTORS</div>
                    <div className="space-y-1">
                      {sectors.filter(s => s.rotation_signal === 'NEUTRAL').length > 0 ? (
                        sectors.filter(s => s.rotation_signal === 'NEUTRAL').map(s => (
                          <div key={s.sector} className="text-xs text-nzt-text flex justify-between">
                            <span>{sectorDisplayName(s.sector)}</span>
                            <span className="font-mono text-nzt-muted">{fmt(s.capital_inflow_score, 0)}</span>
                          </div>
                        ))
                      ) : (
                        <div className="text-[10px] text-nzt-muted">None</div>
                      )}
                    </div>
                  </div>
                  <div className="bg-red-900/20 rounded-lg border border-red-500/30 p-3">
                    <div className="text-[10px] text-red-400 font-bold mb-1">OUTFLOW SECTORS</div>
                    <div className="space-y-1">
                      {sectors.filter(s => s.rotation_signal === 'OUTFLOW').length > 0 ? (
                        sectors.filter(s => s.rotation_signal === 'OUTFLOW').map(s => (
                          <div key={s.sector} className="text-xs text-nzt-text flex justify-between">
                            <span>{sectorDisplayName(s.sector)}</span>
                            <span className="font-mono text-red-400">{fmt(s.capital_inflow_score, 0)}</span>
                          </div>
                        ))
                      ) : (
                        <div className="text-[10px] text-nzt-muted">None</div>
                      )}
                    </div>
                  </div>
                </div>
              </>
            ) : (
              <div className="text-center text-nzt-muted text-xs py-12">No money flow data available yet.</div>
            )}
          </Card>
        </div>
      )}

      {/* ========== BEST INSTRUMENTS TAB ========== */}
      {tab === 'instruments' && (
        <div className="space-y-3">
          <Card title="BEST ISA ETP PER SECTOR">
            <div className="text-[10px] text-nzt-muted mb-3" title="For each sector, the single instrument with the highest recent momentum score. These are the most actionable LSE leveraged ETPs.">
              Top-ranked instrument per sector by short-term momentum. Click any ticker for full analysis.
            </div>
            {sectors.length > 0 ? (
              <>
                <div className="grid grid-cols-2 gap-3">
                  {sectors.map(s => (
                    <div key={s.sector} className="bg-nzt-bg rounded-lg border border-nzt-border p-3 hover:border-nzt-accent/30 transition">
                      <div className="flex items-center justify-between mb-2">
                        <span className="text-[11px] font-bold text-nzt-text">{sectorDisplayName(s.sector)}</span>
                        <SectorSignalBadge signal={s.leadership_status} />
                      </div>
                      <div className="flex items-center gap-3 mb-2">
                        <TickerLink ticker={s.best_instrument} className="text-lg font-bold" />
                        <span className={clsx('text-xs font-mono',
                          s.best_instrument_score >= 0 ? 'text-nzt-accent' : 'text-nzt-danger'
                        )}>
                          {s.best_instrument_score >= 0 ? '+' : ''}{fmt(s.best_instrument_score, 1)}%
                        </span>
                      </div>
                      {/* All instruments in sector */}
                      <div className="flex flex-wrap gap-1">
                        {s.instruments.map(ticker => (
                          <TickerLink
                            key={ticker}
                            ticker={ticker}
                            className={clsx(
                              'text-[10px] px-1.5 py-0.5 rounded bg-nzt-card border border-nzt-border',
                              ticker === s.best_instrument ? 'border-nzt-accent/50 text-nzt-accent' : 'text-nzt-muted'
                            )}
                          />
                        ))}
                      </div>
                      {/* Score bars */}
                      <div className="mt-2 space-y-0.5">
                        <ScoreBar label="Composite" value={s.composite_score} />
                        <ScoreBar label="Momentum" value={s.momentum_score} />
                        <ScoreBar label="Inflow" value={s.capital_inflow_score} />
                      </div>
                    </div>
                  ))}
                </div>

                {/* Quick-pick summary */}
                <div className="mt-4 bg-nzt-card rounded-lg border border-nzt-accent/30 p-3">
                  <div className="text-[10px] text-nzt-accent font-bold mb-2 uppercase tracking-wider">Quick Pick: Top 3 Instruments Right Now</div>
                  <div className="flex gap-4">
                    {sectors.slice(0, 3).map(s => (
                      <div key={s.sector} className="flex items-center gap-2">
                        <span className="text-[10px] text-nzt-muted">#{s.rank}</span>
                        <TickerLink ticker={s.best_instrument} className="text-sm font-bold" />
                        <span className="text-[10px] text-nzt-muted">({sectorDisplayName(s.sector)})</span>
                      </div>
                    ))}
                  </div>
                </div>
              </>
            ) : (
              <div className="text-center text-nzt-muted text-xs py-12">No instrument data available yet.</div>
            )}
          </Card>
        </div>
      )}

      {/* ========== REGIME x SECTOR PERFORMANCE MATRIX TAB ========== */}
      {tab === 'regime-matrix' && (
        <div className="space-y-3">
          <Card title="REGIME x SECTOR PERFORMANCE MATRIX">
            <div className="text-[10px] text-nzt-muted mb-3" title="Shows which sectors typically outperform in each market regime. Current regime is highlighted. Helps identify which sectors to focus on given market conditions.">
              Structural sector-regime affinity map. Highlighted column = current market regime. Green cells = sectors that thrive in that regime.
            </div>
            {sectors.length > 0 ? (
              <>
                {/* Matrix table */}
                <div className="overflow-x-auto">
                  <table className="w-full text-xs">
                    <thead>
                      <tr className="border-b border-nzt-border">
                        <th className="py-2 px-3 text-left text-nzt-muted">Sector</th>
                        {REGIME_NAMES.map(r => (
                          <th key={r} className={clsx(
                            'py-2 px-3 text-center font-bold',
                            currentRegime === r ? 'text-nzt-accent bg-nzt-accent/5' : 'text-nzt-muted'
                          )}>
                            {r.replace(/_/g, ' ')}
                            {currentRegime === r && <span className="ml-1 text-[9px]">(NOW)</span>}
                          </th>
                        ))}
                        <th className="py-2 px-3 text-center text-nzt-muted">Current Score</th>
                        <th className="py-2 px-3 text-center text-nzt-muted">Best ETP</th>
                      </tr>
                    </thead>
                    <tbody>
                      {sectors.map(s => (
                        <tr key={s.sector} className="border-b border-nzt-border/20 hover:bg-nzt-bg/50">
                          <td className="py-2 px-3 font-bold text-nzt-text">{sectorDisplayName(s.sector)}</td>
                          {REGIME_NAMES.map(r => {
                            const affinity = REGIME_SECTOR_AFFINITY[r]?.includes(s.sector)
                            const isCurrentRegime = currentRegime === r
                            return (
                              <td key={r} className={clsx(
                                'py-2 px-3 text-center',
                                isCurrentRegime ? 'bg-nzt-accent/5' : '',
                              )}>
                                {affinity ? (
                                  <span className={clsx(
                                    'inline-block w-6 h-6 rounded-full leading-6 text-center text-[10px] font-bold',
                                    isCurrentRegime && affinity
                                      ? 'bg-green-500/30 text-green-400 ring-1 ring-green-400'
                                      : 'bg-green-900/30 text-green-500/70'
                                  )}>
                                    {'\u2713'}
                                  </span>
                                ) : (
                                  <span className="text-nzt-border">{'\u2014'}</span>
                                )}
                              </td>
                            )
                          })}
                          <td className={clsx('py-2 px-3 text-center font-mono font-bold',
                            s.composite_score >= 60 ? 'text-nzt-accent' : s.composite_score >= 40 ? 'text-yellow-400' : 'text-nzt-danger'
                          )}>
                            {fmt(s.composite_score, 0)}
                          </td>
                          <td className="py-2 px-3 text-center">
                            <TickerLink ticker={s.best_instrument} className="text-xs" />
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>

                {/* Regime insight cards */}
                <div className="grid grid-cols-3 gap-3 mt-4">
                  {REGIME_NAMES.map(r => {
                    const isActive = currentRegime === r
                    const affinitySectors = sectors.filter(s => REGIME_SECTOR_AFFINITY[r]?.includes(s.sector))
                    const avgScore = affinitySectors.length > 0
                      ? affinitySectors.reduce((sum, s) => sum + s.composite_score, 0) / affinitySectors.length
                      : 0
                    return (
                      <div key={r} className={clsx(
                        'rounded-lg border p-3',
                        isActive ? 'border-nzt-accent/50 bg-nzt-accent/5' : 'border-nzt-border bg-nzt-bg'
                      )}>
                        <div className="flex items-center justify-between mb-2">
                          <span className={clsx('text-xs font-bold', isActive ? 'text-nzt-accent' : 'text-nzt-muted')}>
                            {r.replace(/_/g, ' ')}
                          </span>
                          {isActive && <span className="text-[9px] px-1.5 py-0.5 rounded bg-nzt-accent/20 text-nzt-accent font-bold">ACTIVE</span>}
                        </div>
                        <div className="text-[10px] text-nzt-muted mb-1">Favoured sectors:</div>
                        <div className="space-y-1">
                          {affinitySectors.length > 0 ? (
                            affinitySectors.map(s => (
                              <div key={s.sector} className="flex items-center justify-between text-[11px]">
                                <span className="text-nzt-text">{sectorDisplayName(s.sector)}</span>
                                <span className={clsx('font-mono',
                                  s.composite_score >= 60 ? 'text-nzt-accent' : 'text-nzt-muted'
                                )}>{fmt(s.composite_score, 0)}</span>
                              </div>
                            ))
                          ) : (
                            <div className="text-[10px] text-nzt-muted">No specific affinity sectors</div>
                          )}
                        </div>
                        <div className="mt-2 pt-2 border-t border-nzt-border/50 flex justify-between text-[10px]">
                          <span className="text-nzt-muted">Avg Score</span>
                          <span className={clsx('font-mono font-bold',
                            avgScore >= 60 ? 'text-nzt-accent' : avgScore >= 40 ? 'text-yellow-400' : 'text-nzt-danger'
                          )}>{fmt(avgScore, 0)}</span>
                        </div>
                      </div>
                    )
                  })}
                </div>
              </>
            ) : (
              <div className="text-center text-nzt-muted text-xs py-12">No sector or regime data available yet.</div>
            )}
          </Card>
        </div>
      )}

      {/* Last updated */}
      {snapshot?.generated_at && (
        <div className="mt-3 text-right text-[10px] text-nzt-muted">
          Last scan: {timeAgo(snapshot.generated_at)}
        </div>
      )}
    </div>
  )
}
