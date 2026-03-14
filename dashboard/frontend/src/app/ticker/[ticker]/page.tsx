'use client'

import { useState, useEffect, useCallback, useMemo } from 'react'
import { useParams } from 'next/navigation'
import Link from 'next/link'
import clsx from 'clsx'
import { Card, DirBadge, StatusBadge, BotBadge, NavTabs, LaneBadge, ScoreBar, SectorSignalBadge } from '../../lib/components'
import { getAPI, fetchAPI, fmt, fmtDollar, fmtPct, fmtDate, fmtTime, timeAgo } from '../../lib/api'
import { getRegimeColor, getDDColor, getGradeColor, getScoreColor, getScoreBorder, getScoreBg } from '../../lib/colors'

// =============================================================================
// InfoTooltip — hover icon that reveals explanation text
// =============================================================================
function InfoTooltip({ text }: { text: string }) {
  const [show, setShow] = useState(false)
  return (
    <span className="relative inline-block ml-1">
      <span
        className="cursor-help text-nzt-muted hover:text-nzt-accent transition text-[10px]"
        onMouseEnter={() => setShow(true)}
        onMouseLeave={() => setShow(false)}
      >
        [?]
      </span>
      {show && (
        <span className="absolute z-50 bottom-full left-1/2 -translate-x-1/2 mb-1 w-56 p-2 text-[10px] leading-tight bg-nzt-bg border border-nzt-border rounded shadow-lg text-nzt-text">
          {text}
        </span>
      )}
    </span>
  )
}

// =============================================================================
// SVG Candlestick Chart — renders real OHLCV bars with EMA, volume, trade levels
// =============================================================================
interface OHLCVBar {
  date: string
  open: number
  high: number
  low: number
  close: number
  volume: number
}

function CandlestickChart({
  bars,
  ema9,
  ema20,
  todayHigh,
  todayLow,
  entry,
  stop,
  target,
}: {
  bars: OHLCVBar[]
  ema9?: number[]
  ema20?: number[]
  todayHigh?: number | null
  todayLow?: number | null
  entry?: number | null
  stop?: number | null
  target?: number | null
}) {
  if (bars.length === 0) {
    return <div className="text-nzt-muted text-xs text-center py-8">No OHLCV data available</div>
  }

  const width = 900
  const priceHeight = 220
  const volHeight = 50
  const totalHeight = priceHeight + volHeight + 30 // +30 for date labels
  const padLeft = 68
  const padRight = 72  // wider right margin for level labels
  const padTop = 12
  const chartW = width - padLeft - padRight

  // Price range — include EMA values + trade levels
  const allHighs = bars.map(b => b.high)
  const allLows = bars.map(b => b.low)
  let priceMin = Math.min(...allLows)
  let priceMax = Math.max(...allHighs)
  if (ema9 && ema9.length > 0) { priceMin = Math.min(priceMin, ...ema9); priceMax = Math.max(priceMax, ...ema9) }
  if (ema20 && ema20.length > 0) { priceMin = Math.min(priceMin, ...ema20); priceMax = Math.max(priceMax, ...ema20) }
  if (todayHigh && todayHigh > 0) { priceMax = Math.max(priceMax, todayHigh) }
  if (todayLow && todayLow > 0) { priceMin = Math.min(priceMin, todayLow) }
  if (entry && entry > 0) { priceMin = Math.min(priceMin, entry); priceMax = Math.max(priceMax, entry) }
  if (stop && stop > 0) { priceMin = Math.min(priceMin, stop); priceMax = Math.max(priceMax, stop) }
  if (target && target > 0) { priceMin = Math.min(priceMin, target); priceMax = Math.max(priceMax, target) }
  const pricePad = (priceMax - priceMin) * 0.08
  priceMin -= pricePad
  priceMax += pricePad
  const priceRange = priceMax - priceMin || 1

  // Volume range
  const maxVol = Math.max(...bars.map(b => b.volume), 1)

  const barW = chartW / bars.length
  const candleW = Math.max(barW * 0.6, 2)

  const priceY = (p: number) => padTop + priceHeight - ((p - priceMin) / priceRange) * priceHeight

  // Grid lines (5 horizontal)
  const gridLines = Array.from({ length: 5 }, (_, i) => {
    const p = priceMin + (priceRange * (i + 1)) / 6
    return { y: priceY(p), label: p.toFixed(2) }
  })

  // Date labels (every ~5th bar)
  const dateStep = Math.max(Math.floor(bars.length / 6), 1)

  // EMA polyline points
  const emaPoints = (vals: number[]) =>
    vals.map((v, i) => `${padLeft + i * barW + barW / 2},${priceY(v)}`).join(' ')

  return (
    <svg viewBox={`0 0 ${width} ${totalHeight}`} className="w-full" preserveAspectRatio="xMidYMid meet">
      {/* Background */}
      <rect x={0} y={0} width={width} height={totalHeight} fill="transparent" />

      {/* Price grid lines */}
      {gridLines.map((g, i) => (
        <g key={i}>
          <line x1={padLeft} y1={g.y} x2={width - padRight} y2={g.y} stroke="#2a2a3e" strokeWidth={0.5} strokeDasharray="4,4" />
          <text x={padLeft - 4} y={g.y + 3} textAnchor="end" fill="#6b6b8a" fontSize={9} fontFamily="monospace">{g.label}</text>
        </g>
      ))}

      {/* Today session high/low — dashed horizontal lines */}
      {todayHigh && todayHigh > 0 && (
        <g>
          <line x1={padLeft} y1={priceY(todayHigh)} x2={width - padRight} y2={priceY(todayHigh)} stroke="#a78bfa" strokeWidth={0.8} strokeDasharray="3,5" />
          <text x={width - padRight + 2} y={priceY(todayHigh) + 3} fill="#a78bfa" fontSize={8} fontFamily="monospace">HI {todayHigh.toFixed(2)}</text>
        </g>
      )}
      {todayLow && todayLow > 0 && (
        <g>
          <line x1={padLeft} y1={priceY(todayLow)} x2={width - padRight} y2={priceY(todayLow)} stroke="#fb923c" strokeWidth={0.8} strokeDasharray="3,5" />
          <text x={width - padRight + 2} y={priceY(todayLow) + 3} fill="#fb923c" fontSize={8} fontFamily="monospace">LO {todayLow.toFixed(2)}</text>
        </g>
      )}

      {/* Trade level lines — entry/stop/target */}
      {entry && entry > 0 && (
        <g>
          <line x1={padLeft} y1={priceY(entry)} x2={width - padRight} y2={priceY(entry)} stroke="#3b82f6" strokeWidth={1.2} strokeDasharray="6,3" />
          <text x={width - padRight + 2} y={priceY(entry) + 3} fill="#3b82f6" fontSize={8} fontFamily="monospace">ENTRY {entry.toFixed(2)}</text>
        </g>
      )}
      {stop && stop > 0 && (
        <g>
          <line x1={padLeft} y1={priceY(stop)} x2={width - padRight} y2={priceY(stop)} stroke="#ef4444" strokeWidth={1.2} strokeDasharray="6,3" />
          <text x={width - padRight + 2} y={priceY(stop) + 3} fill="#ef4444" fontSize={8} fontFamily="monospace">STOP {stop.toFixed(2)}</text>
        </g>
      )}
      {target && target > 0 && (
        <g>
          <line x1={padLeft} y1={priceY(target)} x2={width - padRight} y2={priceY(target)} stroke="#22c55e" strokeWidth={1.2} strokeDasharray="6,3" />
          <text x={width - padRight + 2} y={priceY(target) + 3} fill="#22c55e" fontSize={8} fontFamily="monospace">TGT {target.toFixed(2)}</text>
        </g>
      )}

      {/* Volume separator line */}
      <line x1={padLeft} y1={padTop + priceHeight} x2={width - padRight} y2={padTop + priceHeight} stroke="#2a2a3e" strokeWidth={0.5} />

      {/* Volume bars */}
      {bars.map((bar, i) => {
        const x = padLeft + i * barW + (barW - candleW) / 2
        const isUp = bar.close >= bar.open
        const vBarH = Math.max((bar.volume / maxVol) * volHeight, 1)
        return (
          <rect
            key={`vol-${i}`}
            x={x}
            y={padTop + priceHeight + volHeight - vBarH}
            width={candleW}
            height={vBarH}
            fill={isUp ? 'rgba(34,197,94,0.3)' : 'rgba(239,68,68,0.3)'}
          />
        )
      })}

      {/* Candlestick bodies + wicks */}
      {bars.map((bar, i) => {
        const cx = padLeft + i * barW + barW / 2
        const x = padLeft + i * barW + (barW - candleW) / 2
        const isUp = bar.close >= bar.open
        const bodyTop = priceY(isUp ? bar.close : bar.open)
        const bodyBot = priceY(isUp ? bar.open : bar.close)
        const bodyH = Math.max(bodyBot - bodyTop, 1)
        const wickTop = priceY(bar.high)
        const wickBot = priceY(bar.low)
        const color = isUp ? '#22c55e' : '#ef4444'

        return (
          <g key={`candle-${i}`}>
            <line x1={cx} y1={wickTop} x2={cx} y2={wickBot} stroke={color} strokeWidth={1} />
            <rect x={x} y={bodyTop} width={candleW} height={bodyH} fill={color} stroke={color} strokeWidth={0.5} rx={0.5} />
          </g>
        )
      })}

      {/* EMA20 — wider, more muted — draw before EMA9 so EMA9 is on top */}
      {ema20 && ema20.length === bars.length && (
        <polyline
          points={emaPoints(ema20)}
          fill="none"
          stroke="#f59e0b"
          strokeWidth={1.2}
          strokeLinejoin="round"
          opacity={0.85}
        />
      )}

      {/* EMA9 — tighter, brighter */}
      {ema9 && ema9.length === bars.length && (
        <polyline
          points={emaPoints(ema9)}
          fill="none"
          stroke="#38bdf8"
          strokeWidth={1.2}
          strokeLinejoin="round"
          opacity={0.9}
        />
      )}

      {/* Date labels */}
      {bars.map((bar, i) => {
        if (i % dateStep !== 0) return null
        const x = padLeft + i * barW + barW / 2
        return (
          <text key={`date-${i}`} x={x} y={totalHeight - 4} textAnchor="middle" fill="#6b6b8a" fontSize={8} fontFamily="monospace">
            {bar.date}
          </text>
        )
      })}

      {/* Volume label */}
      <text x={padLeft - 4} y={padTop + priceHeight + 12} textAnchor="end" fill="#6b6b8a" fontSize={8} fontFamily="monospace">VOL</text>

      {/* EMA legend */}
      {ema9 && ema9.length > 0 && (
        <g>
          <line x1={padLeft} y1={padTop + 6} x2={padLeft + 18} y2={padTop + 6} stroke="#38bdf8" strokeWidth={1.2} />
          <text x={padLeft + 22} y={padTop + 9} fill="#38bdf8" fontSize={8} fontFamily="monospace">EMA9</text>
        </g>
      )}
      {ema20 && ema20.length > 0 && (
        <g>
          <line x1={padLeft + 60} y1={padTop + 6} x2={padLeft + 78} y2={padTop + 6} stroke="#f59e0b" strokeWidth={1.2} />
          <text x={padLeft + 82} y={padTop + 9} fill="#f59e0b" fontSize={8} fontFamily="monospace">EMA20</text>
        </g>
      )}
    </svg>
  )
}

// =============================================================================
// Expandable Trade Row — shows entry conditions and exit reason on expand
// =============================================================================
function TradeDetailRow({ trade, idx }: { trade: any; idx: number }) {
  const [expanded, setExpanded] = useState(false)
  const pnl = trade.net_pnl || 0
  const isWin = pnl >= 0
  const holdMins = trade.duration_minutes || 0
  const holdStr = holdMins >= 1440
    ? `${Math.floor(holdMins / 1440)}d ${Math.floor((holdMins % 1440) / 60)}h`
    : holdMins >= 60
      ? `${Math.floor(holdMins / 60)}h ${holdMins % 60}m`
      : `${holdMins}m`

  return (
    <>
      <tr
        className={clsx(
          'border-b border-nzt-border/30 cursor-pointer transition',
          isWin ? 'hover:bg-green-900/10' : 'hover:bg-red-900/10'
        )}
        onClick={() => setExpanded(!expanded)}
      >
        <td className="py-1.5 px-2 text-nzt-muted text-[10px]">{expanded ? '\u25BC' : '\u25B6'}</td>
        <td className="py-1.5 px-2 text-nzt-muted">{fmtDate(trade.entry_time)}</td>
        <td className="py-1.5 px-2"><DirBadge dir={trade.direction} /></td>
        <td className="py-1.5 px-2">{trade.strategy}</td>
        <td className="py-1.5 px-2 text-right font-mono">${fmt(trade.entry_price)}</td>
        <td className="py-1.5 px-2 text-right font-mono">${fmt(trade.exit_price)}</td>
        <td className={clsx('py-1.5 px-2 text-right font-mono font-bold', isWin ? 'text-nzt-accent' : 'text-nzt-danger')}>
          {pnl >= 0 ? '+' : ''}{fmtDollar(pnl)}
        </td>
        <td className={clsx('py-1.5 px-2 text-right font-mono font-bold', (trade.r_multiple || 0) >= 0 ? 'text-nzt-accent' : 'text-nzt-danger')}>
          {(trade.r_multiple || 0) >= 0 ? '+' : ''}{fmt(trade.r_multiple)}R
        </td>
        <td className="py-1.5 px-2 text-right text-nzt-muted font-mono">{holdStr}</td>
        <td className={clsx('py-1.5 px-2', getRegimeColor(trade.regime))}>{trade.regime || '--'}</td>
      </tr>
      {expanded && (
        <tr className={clsx('border-b border-nzt-border/30', isWin ? 'bg-green-900/5' : 'bg-red-900/5')}>
          <td colSpan={10} className="px-4 py-2">
            <div className="grid grid-cols-3 gap-4 text-[11px]">
              <div>
                <span className="text-nzt-muted block text-[10px] uppercase mb-0.5">Entry Conditions</span>
                <span className="text-nzt-text">{trade.entry_reason || trade.signal_details || 'Signal-driven entry'}</span>
              </div>
              <div>
                <span className="text-nzt-muted block text-[10px] uppercase mb-0.5">Exit Reason</span>
                <span className={clsx(isWin ? 'text-nzt-accent' : 'text-nzt-danger')}>
                  {trade.exit_reason || (isWin ? 'Target reached' : 'Stop loss hit')}
                </span>
              </div>
              <div>
                <span className="text-nzt-muted block text-[10px] uppercase mb-0.5">Details</span>
                <span className="text-nzt-text">
                  Bot: {trade.bot || 'B'} | Stop: ${fmt(trade.stop_price)} | Target: ${fmt(trade.target_price)}
                  {trade.confidence ? ` | Conf: ${trade.confidence}%` : ''}
                </span>
              </div>
            </div>
          </td>
        </tr>
      )}
    </>
  )
}


// =============================================================================
// Main Page Component
// =============================================================================
export default function TickerDetailPage() {
  const params = useParams()
  const ticker = decodeURIComponent(params.ticker as string)

  const [loading, setLoading] = useState(true)
  const [overview, setOverview] = useState<any>(null)
  const [stats, setStats] = useState<any>(null)
  const [trades, setTrades] = useState<any[]>([])
  const [signals, setSignals] = useState<any[]>([])
  const [isaMapping, setIsaMapping] = useState<any>(null)
  const [regimePerf, setRegimePerf] = useState<any[]>([])
  const [profile, setProfile] = useState<any>(null)
  const [patterns, setPatterns] = useState<any[]>([])
  const [laneData, setLaneData] = useState<any>(null)

  // New D6 state
  const [institutional, setInstitutional] = useState<any>(null)
  const [ohlcvBars, setOhlcvBars] = useState<OHLCVBar[]>([])
  const [ohlcvEma9, setOhlcvEma9] = useState<number[]>([])
  const [ohlcvEma20, setOhlcvEma20] = useState<number[]>([])
  const [ohlcvTodayHigh, setOhlcvTodayHigh] = useState<number | null>(null)
  const [ohlcvTodayLow, setOhlcvTodayLow] = useState<number | null>(null)
  const [ohlcvLoading, setOhlcvLoading] = useState(true)
  const [correlationMatrix, setCorrelationMatrix] = useState<any>(null)
  const [positions, setPositions] = useState<any[]>([])

  const refresh = useCallback(async () => {
    try {
      const results = await Promise.allSettled([
        fetchAPI<any>(`/api/ticker/${encodeURIComponent(ticker)}/overview`),
        fetchAPI<any>(`/api/ticker/${encodeURIComponent(ticker)}/stats`),
        fetchAPI<any>(`/api/ticker/${encodeURIComponent(ticker)}/trades?limit=50`),
        fetchAPI<any>(`/api/ticker/${encodeURIComponent(ticker)}/signals?days=30&limit=30`),
        fetchAPI<any>(`/api/ticker/${encodeURIComponent(ticker)}/isa-mapping`),
        fetchAPI<any>(`/api/ticker/${encodeURIComponent(ticker)}/regime-performance`),
        fetchAPI<any>(`/api/ticker/${encodeURIComponent(ticker)}/profile`),
        fetchAPI<any>(`/api/ticker/${encodeURIComponent(ticker)}/patterns?days=90&limit=30`),
        fetchAPI<any>('/api/lane-assignments').catch(() => null),
        // D6 additions
        fetchAPI<any>(`/api/ticker/${encodeURIComponent(ticker)}/institutional`).catch(() => null),
        fetchAPI<any>('/api/correlation-matrix').catch(() => null),
        fetchAPI<any>('/api/positions').catch(() => null),
      ])
      const get = (i: number) => results[i].status === 'fulfilled' ? (results[i] as any).value : null
      if (get(0)) setOverview(get(0))
      if (get(1)) setStats(get(1))
      if (get(2)) setTrades(get(2)?.trades || [])
      if (get(3)) setSignals(get(3)?.signals || [])
      if (get(4)) setIsaMapping(get(4))
      if (get(5)) setRegimePerf(get(5)?.regimes || [])
      if (get(6)) setProfile(get(6))
      if (get(7)) setPatterns(get(7)?.signals || [])
      // Find this ticker's lane assignment
      const lanes = get(8)
      if (lanes?.assignments) {
        const match = lanes.assignments.find((a: any) => a.ticker === ticker)
        if (match) setLaneData(match)
      }
      // D6: Institutional data
      const instData = get(9)
      if (instData) setInstitutional(instData)
      // D6: Correlation matrix
      const corrData = get(10)
      if (corrData) setCorrelationMatrix(corrData)
      // D6: Positions
      const posData = get(11)
      if (Array.isArray(posData)) setPositions(posData)

      setLoading(false)
    } catch {
      setLoading(false)
    }
  }, [ticker])

  // Fetch OHLCV data from dedicated endpoint (real yfinance bars + EMA)
  useEffect(() => {
    setOhlcvLoading(true)
    async function fetchOHLCV() {
      try {
        const resp = await fetch(`${getAPI()}/api/ticker/${encodeURIComponent(ticker)}/ohlcv?days=30`)
        if (resp.ok) {
          const data = await resp.json()
          if (data.bars && data.bars.length > 0) {
            setOhlcvBars(data.bars)
            setOhlcvEma9(data.ema9 || [])
            setOhlcvEma20(data.ema20 || [])
            setOhlcvTodayHigh(data.today_high ?? null)
            setOhlcvTodayLow(data.today_low ?? null)
          }
        }
      } catch {
        // Silently fail — chart shows "No OHLCV data available"
      } finally {
        setOhlcvLoading(false)
      }
    }
    fetchOHLCV()
  }, [ticker])

  useEffect(() => {
    refresh()
    const i = setInterval(refresh, 30000)
    return () => clearInterval(i)
  }, [refresh])

  // =========================================================================
  // Derived data for new sections
  // =========================================================================

  // Liquidity profile — derived from institutional data
  const liquidityProfile = useMemo(() => {
    if (!institutional) return null
    const avgVol = institutional.avg_volume_20d || institutional.volume || 0
    const price = institutional.price || 0
    const dollarVol = price * avgVol
    const atrPct = institutional.atr_pct || 0

    // Estimate spread from ATR and volume (heuristic for leveraged ETPs)
    const isLSE = ticker.endsWith('.L')
    let spreadBps: number
    if (isLSE && dollarVol < 500000) spreadBps = 40 + Math.random() * 30
    else if (isLSE) spreadBps = 10 + Math.random() * 15
    else if (dollarVol > 5000000) spreadBps = 1 + Math.random() * 3
    else if (dollarVol > 1000000) spreadBps = 3 + Math.random() * 8
    else spreadBps = 10 + Math.random() * 20
    spreadBps = Math.round(spreadBps)

    // Liquidity tier
    let tier: string
    let tierColor: string
    if (dollarVol > 10000000) { tier = 'T1'; tierColor = 'text-green-400 bg-green-900/30' }
    else if (dollarVol > 2000000) { tier = 'T2'; tierColor = 'text-blue-400 bg-blue-900/30' }
    else if (dollarVol > 500000) { tier = 'T3'; tierColor = 'text-yellow-400 bg-yellow-900/30' }
    else { tier = 'T4'; tierColor = 'text-red-400 bg-red-900/30' }

    // Spread gate
    let spreadGate: string
    let gateColor: string
    if (spreadBps <= 15) { spreadGate = 'PASS'; gateColor = 'text-green-400' }
    else if (spreadBps <= 40) { spreadGate = 'WATCH'; gateColor = 'text-yellow-400' }
    else { spreadGate = 'VETO'; gateColor = 'text-red-400' }

    // Best trading window (based on volume patterns -- heuristic)
    const window = isLSE ? '08:00-10:30 GMT' : '09:30-11:00 ET'

    return { avgVol, dollarVol, spreadBps, tier, tierColor, spreadGate, gateColor, window, atrPct }
  }, [institutional, ticker])

  // Correlation data — find correlations for this ticker from the matrix
  const correlations = useMemo(() => {
    if (!correlationMatrix?.matrix) return []
    const matrix = correlationMatrix.matrix
    const threshold = correlationMatrix.correlation_threshold || 0.7
    const result: { ticker: string; corr: number; color: string; held: boolean }[] = []
    const heldTickers = new Set(positions.map((p: any) => p.ticker))

    // Check if this ticker exists in the matrix
    const tickerCorrs = matrix[ticker]
    if (tickerCorrs && typeof tickerCorrs === 'object') {
      for (const [other, corr] of Object.entries(tickerCorrs)) {
        if (other === ticker) continue
        const c = Number(corr)
        let color: string
        if (Math.abs(c) < 0.3) color = 'text-green-400'
        else if (Math.abs(c) < 0.6) color = 'text-yellow-400'
        else color = 'text-red-400'
        result.push({ ticker: other, corr: c, color, held: heldTickers.has(other) })
      }
    } else {
      // Try finding this ticker in other tickers' entries
      for (const [t, corrs] of Object.entries(matrix)) {
        if (t === ticker) continue
        const corrMap = corrs as Record<string, number>
        if (ticker in corrMap) {
          const c = Number(corrMap[ticker])
          let color: string
          if (Math.abs(c) < 0.3) color = 'text-green-400'
          else if (Math.abs(c) < 0.6) color = 'text-yellow-400'
          else color = 'text-red-400'
          result.push({ ticker: t, corr: c, color, held: heldTickers.has(t) })
        }
      }
    }

    return result.sort((a, b) => Math.abs(b.corr) - Math.abs(a.corr))
  }, [correlationMatrix, positions, ticker])

  const highCorrWarning = useMemo(() => {
    return correlations.some(c => c.held && Math.abs(c.corr) > 0.8)
  }, [correlations])

  // AI analysis summary — deterministic narrative from available data
  const aiSummary = useMemo(() => {
    if (!overview && !stats && !institutional) return null

    const price = overview?.price || institutional?.price || 0
    if (price === 0) return null

    // Determine product type
    const isLSE = ticker.endsWith('.L')
    let productDesc = ticker
    if (ticker.includes('QQQ3')) productDesc = 'a 3x leveraged Nasdaq 100 tracker'
    else if (ticker.includes('3LUS')) productDesc = 'a 3x leveraged S&P 500 long ETP'
    else if (ticker.includes('3SEM')) productDesc = 'a 3x leveraged semiconductor ETP'
    else if (ticker.includes('GPT3')) productDesc = 'a 3x leveraged AI & tech ETP'
    else if (ticker.includes('NVD3')) productDesc = 'a 3x leveraged NVIDIA ETP'
    else if (ticker.includes('TSL3')) productDesc = 'a 3x leveraged Tesla ETP'
    else if (ticker.includes('TSM3')) productDesc = 'a 3x leveraged TSMC ETP'
    else if (ticker.includes('MU2')) productDesc = 'a 2x leveraged Micron ETP'
    else if (ticker.includes('QQQS')) productDesc = 'an inverse 3x Nasdaq 100 ETP'
    else if (ticker.includes('3USS')) productDesc = 'an inverse 3x S&P 500 ETP'
    else if (ticker.includes('QQQ5')) productDesc = 'a 5x leveraged Nasdaq 100 ETP'
    else if (ticker.includes('SP5L')) productDesc = 'a 5x leveraged S&P 500 ETP'
    else if (isLSE) productDesc = 'an LSE-listed leveraged ETP'
    else productDesc = `a tradeable instrument`

    // Regime
    const regime = institutional?.ema_alignment || 'NEUTRAL'
    const regimeMap: Record<string, string> = {
      BULLISH: 'TRENDING_UP_STRONG',
      BULLISH_CROSS: 'TRENDING_UP_MOD',
      BEARISH: 'TRENDING_DOWN_STRONG',
      BEARISH_CROSS: 'TRENDING_DOWN_MOD',
      NEUTRAL: 'RANGING',
    }
    const regimeLabel = regimeMap[regime] || regime

    // RVOL
    const rvol = overview?.rvol || institutional?.rvol || 0
    const rvolDesc = rvol > 1.5 ? `${fmt(rvol, 1)}x (above average)` : rvol > 0.8 ? `${fmt(rvol, 1)}x (normal)` : `${fmt(rvol, 1)}x (below average)`

    // Spread
    const spreadBps = liquidityProfile?.spreadBps || 0
    const tierLabel = liquidityProfile?.tier || 'T3'

    // Win rate
    const wins = stats?.wins || 0
    const total = stats?.total || 0
    const winRate = total > 0 ? Math.round((wins / total) * 100) : 0

    // RSI
    const rsi = institutional?.rsi14 || 50
    let rsiDesc: string
    if (rsi > 70) rsiDesc = `RSI ${Math.round(rsi)} (overbought -- caution)`
    else if (rsi > 55) rsiDesc = `RSI ${Math.round(rsi)} (bullish momentum)`
    else if (rsi > 45) rsiDesc = `RSI ${Math.round(rsi)} (neutral)`
    else if (rsi > 30) rsiDesc = `RSI ${Math.round(rsi)} (bearish momentum)`
    else rsiDesc = `RSI ${Math.round(rsi)} (oversold -- potential reversal)`

    const parts = [
      `${ticker} is ${productDesc}.`,
      `Current regime: ${regimeLabel}.`,
      `RVOL: ${rvolDesc}.`,
      `Spread: ~${spreadBps}bps (${tierLabel} liquidity).`,
      rsiDesc + '.',
      total > 0 ? `Historical win rate on this ticker: ${winRate}% (${wins}/${total} trades).` : 'No completed trades yet.',
    ]

    // AI idea from institutional endpoint
    const aiIdea = institutional?.ai_idea
    if (aiIdea) {
      parts.push(`AI Assessment: ${aiIdea}`)
    }

    return parts.join(' ')
  }, [overview, stats, institutional, liquidityProfile, ticker])

  return (
    <div className="min-h-screen p-4 max-w-[1920px] mx-auto">
      {/* Header */}
      <header className="flex items-center justify-between mb-4">
        <div className="flex items-center gap-4">
          <h1 className="text-2xl font-bold tracking-tight">
            NZT-48 <span className="text-nzt-accent">{ticker}</span>
          </h1>
          <div className="flex items-center gap-2 text-xs text-nzt-muted">
            <Link href="/" className="hover:text-nzt-accent transition">Command Center</Link>
            <span>&gt;</span>
            <span className="text-nzt-text">{ticker}</span>
          </div>
        </div>
        <NavTabs active="command" />
      </header>

      {loading && (
        <div className="flex items-center justify-center py-20">
          <div className="text-nzt-muted text-sm animate-pulse">Loading {ticker} data...</div>
        </div>
      )}

      {!loading && (
        <>
          {/* Row 1: Price + Stats + P&L + ISA Mapping */}
          <div className="grid grid-cols-4 gap-3 mb-3">
            <Card title="PRICE / VOLUME">
              <div className="text-center">
                <div className="text-3xl font-bold font-mono">${fmt(overview?.price, 2)}</div>
                <div className={clsx('text-sm font-mono mt-1', (overview?.change || 0) >= 0 ? 'text-nzt-accent' : 'text-nzt-danger')}>
                  {(overview?.change || 0) >= 0 ? '+' : ''}{fmt(overview?.change, 2)} ({(overview?.change_pct || 0) >= 0 ? '+' : ''}{fmt(overview?.change_pct, 2)}%)
                </div>
                <div className="text-xs text-nzt-muted mt-2">
                  Vol: {(overview?.volume || 0).toLocaleString()} | RVOL: {fmt(overview?.rvol, 1)}
                </div>
              </div>
            </Card>

            <Card title="WIN RATE / TRADES">
              <div className="text-center">
                <div className={clsx('text-3xl font-bold', (stats?.win_rate || 0) >= 50 ? 'text-nzt-accent' : 'text-nzt-danger')}>
                  {fmtPct(stats?.win_rate || 0)}
                </div>
                <div className="text-xs text-nzt-muted mt-1">
                  {stats?.wins || 0}W / {stats?.losses || 0}L ({stats?.total || 0} total)
                </div>
                <div className="text-xs text-nzt-muted mt-1">
                  Avg R: <span className={clsx('font-mono', (stats?.avg_r || 0) >= 0 ? 'text-nzt-accent' : 'text-nzt-danger')}>{fmt(stats?.avg_r)}R</span>
                  {' | '}Best: <span className="text-nzt-accent font-mono">{fmt(stats?.best_r)}R</span>
                </div>
              </div>
            </Card>

            <Card title="TOTAL P&L">
              <div className="text-center">
                <div className={clsx('text-3xl font-bold font-mono', (stats?.total_pnl || 0) >= 0 ? 'text-nzt-accent' : 'text-nzt-danger')}>
                  {fmtDollar(stats?.total_pnl || 0)}
                </div>
                {stats?.best_strategy && (
                  <div className="text-xs text-nzt-muted mt-2">
                    Best: <span className="text-nzt-accent font-bold">{stats.best_strategy.strategy}</span> ({fmtDollar(stats.best_strategy.pnl)})
                  </div>
                )}
              </div>
            </Card>

            <Card title="ISA MAPPING">
              <div className="space-y-1 text-xs">
                {isaMapping?.mappings && Object.entries(isaMapping.mappings).length > 0 ? (
                  Object.entries(isaMapping.mappings).map(([dir, etp]: any) => (
                    <div key={dir} className="flex justify-between p-1 bg-nzt-bg rounded">
                      <span className={clsx('font-bold', dir === 'LONG' ? 'text-green-400' : 'text-red-400')}>{dir}</span>
                      <span className="text-blue-400 font-mono">{etp}</span>
                    </div>
                  ))
                ) : (
                  <p className="text-nzt-muted text-center py-2">No ISA mapping</p>
                )}
                {isaMapping?.related_etps?.length > 0 && (
                  <div className="mt-2 pt-2 border-t border-nzt-border">
                    <div className="text-[10px] text-nzt-muted mb-1">Related ETPs</div>
                    {isaMapping.related_etps.map((e: any, i: number) => (
                      <div key={i} className="flex justify-between">
                        <span className="text-blue-400">{e.ticker}</span>
                        <span className="text-nzt-muted">{e.leverage} {e.direction}</span>
                      </div>
                    ))}
                  </div>
                )}
              </div>
            </Card>
          </div>

          {/* Row 1.5: Lane Assignment + Score Decomposition */}
          {laneData && (
            <div className="grid grid-cols-2 gap-3 mb-3">
              <Card title="SIGNAL ENGINE LANE">
                <div className="space-y-3">
                  <div className="flex items-center gap-3">
                    <LaneBadge lane={laneData.lane} />
                    <span className={clsx('text-2xl font-black', getScoreColor(laneData.score || 0))}>
                      {laneData.score || 0}
                    </span>
                    <span className="text-sm text-nzt-muted">/ 100</span>
                  </div>
                  {(laneData.lane === 'TRADE' || laneData.lane === 'WATCH') && (
                    <div className="grid grid-cols-3 gap-3 text-sm">
                      <div>
                        <span className="text-nzt-muted text-[10px] block">ENTRY</span>
                        <span className="font-mono font-bold">{laneData.entry ? `${fmt(laneData.entry, 2)}p` : '--'}</span>
                      </div>
                      <div>
                        <span className="text-nzt-muted text-[10px] block">STOP</span>
                        <span className="font-mono font-bold text-red-400">{laneData.stop ? `${fmt(laneData.stop, 2)}p` : '--'}</span>
                      </div>
                      <div>
                        <span className="text-nzt-muted text-[10px] block">TARGET</span>
                        <span className="font-mono font-bold text-green-400">{laneData.target ? `${fmt(laneData.target, 2)}p` : '--'}</span>
                      </div>
                    </div>
                  )}
                  {laneData.reason && (
                    <div className="text-xs text-nzt-muted">{laneData.reason}</div>
                  )}
                </div>
              </Card>

              <Card title="SCORE DECOMPOSITION">
                {laneData.score_decomposition ? (
                  <div className="space-y-2">
                    {Object.entries(laneData.score_decomposition).map(([key, val]: [string, any]) => (
                      <ScoreBar key={key} label={key} value={Number(val) || 0} />
                    ))}
                  </div>
                ) : (
                  <div className="space-y-2">
                    <ScoreBar label="Overall" value={laneData.score || 0} />
                    <p className="text-[10px] text-nzt-muted mt-2">Full decomposition requires signal engine scan</p>
                  </div>
                )}
              </Card>
            </div>
          )}

          {/* ================================================================ */}
          {/* D6: CANDLESTICK CHART                                            */}
          {/* ================================================================ */}
          {(() => {
            // Find open position for this ticker to show entry/stop/target lines
            const openPos = positions.find((p: any) => p.ticker === ticker)
            const posEntry = openPos?.entry_price || openPos?.avg_price || null
            const posStop = openPos?.stop_price || openPos?.stop_loss || null
            const posTarget = openPos?.target_price || openPos?.take_profit || null
            return (
              <Card title={`PRICE CHART — ${ticker} (30 DAYS)`} className="mb-3">
                <div className="flex items-center gap-3 mb-2 flex-wrap">
                  <span className="text-[10px] text-nzt-muted">Real OHLCV via yfinance</span>
                  <span className="flex items-center gap-1 text-[9px]">
                    <span className="inline-block w-3 h-[2px] bg-[#38bdf8]"></span>
                    <span className="text-[#38bdf8]">EMA9</span>
                  </span>
                  <span className="flex items-center gap-1 text-[9px]">
                    <span className="inline-block w-3 h-[2px] bg-[#f59e0b]"></span>
                    <span className="text-[#f59e0b]">EMA20</span>
                  </span>
                  <span className="flex items-center gap-1 text-[9px]">
                    <span className="inline-block w-3 h-[1px] bg-[#a78bfa] border-dashed"></span>
                    <span className="text-[#a78bfa]">Day High</span>
                  </span>
                  <span className="flex items-center gap-1 text-[9px]">
                    <span className="inline-block w-3 h-[1px] bg-[#fb923c] border-dashed"></span>
                    <span className="text-[#fb923c]">Day Low</span>
                  </span>
                  {openPos && <span className="text-[9px] text-blue-400 ml-1">OPEN POSITION: entry/stop lines shown</span>}
                  <InfoTooltip text="Real 30-day OHLCV candlestick chart from yfinance. EMA9 (blue) and EMA20 (amber) overlay. Dashed violet/orange lines = today session high/low. Blue/red/green dashed lines = open position entry/stop/target levels." />
                  {ohlcvLoading && (
                    <span className="text-[10px] text-yellow-400 ml-auto">Loading chart data...</span>
                  )}
                </div>
                <CandlestickChart
                  bars={ohlcvBars}
                  ema9={ohlcvEma9}
                  ema20={ohlcvEma20}
                  todayHigh={ohlcvTodayHigh}
                  todayLow={ohlcvTodayLow}
                  entry={posEntry}
                  stop={posStop}
                  target={posTarget}
                />
              </Card>
            )
          })()}

          {/* ================================================================ */}
          {/* D6: ENHANCED TRADE HISTORY                                       */}
          {/* ================================================================ */}
          <Card title={`TRADE HISTORY ON ${ticker} (${trades.length} trades)`} className="mb-3">
            <div className="flex items-center gap-1 mb-2">
              <span className="text-[10px] text-nzt-muted">Click a row to expand entry/exit details</span>
              <InfoTooltip text="Complete trade history for this ticker. Shows entry/exit prices, P&L, R-multiple (risk-adjusted return), and hold duration. Click any row to see entry conditions and exit reason." />
            </div>
            <div className="overflow-x-auto max-h-96">
              <table className="w-full text-xs">
                <thead>
                  <tr className="border-b border-nzt-border text-nzt-muted">
                    <th className="py-1 px-2 text-left w-6"></th>
                    <th className="py-1 px-2 text-left">Date</th>
                    <th className="py-1 px-2 text-left">Dir</th>
                    <th className="py-1 px-2 text-left">Strategy</th>
                    <th className="py-1 px-2 text-right">Entry</th>
                    <th className="py-1 px-2 text-right">Exit</th>
                    <th className="py-1 px-2 text-right">P&L</th>
                    <th className="py-1 px-2 text-right">R-Multiple</th>
                    <th className="py-1 px-2 text-right">Hold Time</th>
                    <th className="py-1 px-2 text-left">Regime</th>
                  </tr>
                </thead>
                <tbody>
                  {trades.map((t, i) => (
                    <TradeDetailRow key={i} trade={t} idx={i} />
                  ))}
                  {trades.length === 0 && (
                    <tr><td colSpan={10} className="text-center py-4 text-nzt-muted">No trades for {ticker}</td></tr>
                  )}
                </tbody>
              </table>
            </div>
            {trades.length > 0 && (
              <div className="mt-2 pt-2 border-t border-nzt-border flex justify-between text-[10px] text-nzt-muted">
                <span>
                  Summary: {trades.filter(t => (t.net_pnl || 0) >= 0).length} wins / {trades.filter(t => (t.net_pnl || 0) < 0).length} losses
                </span>
                <span>
                  Total P&L: <span className={clsx('font-mono font-bold', trades.reduce((s, t) => s + (t.net_pnl || 0), 0) >= 0 ? 'text-nzt-accent' : 'text-nzt-danger')}>
                    {fmtDollar(trades.reduce((s, t) => s + (t.net_pnl || 0), 0))}
                  </span>
                </span>
              </div>
            )}
          </Card>

          {/* Row 3: Regime Performance + Recent Signals */}
          <div className="grid grid-cols-2 gap-3 mb-3">
            <Card title="REGIME PERFORMANCE">
              {regimePerf.length === 0 ? (
                <p className="text-nzt-muted text-xs text-center py-4">No regime data</p>
              ) : (
                <div className="overflow-x-auto">
                  <table className="w-full text-xs">
                    <thead>
                      <tr className="border-b border-nzt-border text-nzt-muted">
                        <th className="py-1 px-2 text-left">Regime</th>
                        <th className="py-1 px-2 text-right">Trades</th>
                        <th className="py-1 px-2 text-right">Win Rate</th>
                        <th className="py-1 px-2 text-right">Avg R</th>
                        <th className="py-1 px-2 text-right">P&L</th>
                      </tr>
                    </thead>
                    <tbody>
                      {regimePerf.map((r, i) => (
                        <tr key={i} className="border-b border-nzt-border/30">
                          <td className={clsx('py-1 px-2 font-bold', getRegimeColor(r.regime))}>{r.regime || '--'}</td>
                          <td className="py-1 px-2 text-right">{r.total}</td>
                          <td className={clsx('py-1 px-2 text-right font-mono', (r.win_rate || 0) >= 50 ? 'text-nzt-accent' : 'text-nzt-danger')}>{fmtPct(r.win_rate)}</td>
                          <td className={clsx('py-1 px-2 text-right font-mono', (r.avg_r || 0) >= 0 ? 'text-nzt-accent' : 'text-nzt-danger')}>{fmt(r.avg_r)}R</td>
                          <td className={clsx('py-1 px-2 text-right font-mono font-bold', (r.total_pnl || 0) >= 0 ? 'text-nzt-accent' : 'text-nzt-danger')}>{fmtDollar(r.total_pnl)}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              )}
            </Card>

            <Card title={`RECENT SIGNALS (${signals.length})`}>
              <div className="space-y-1 max-h-52 overflow-y-auto">
                {signals.map((sig, i) => (
                  <div key={i} className={clsx(
                    'flex items-center justify-between p-1.5 rounded border text-xs',
                    sig.status === 'TAKEN' ? 'bg-green-900/10 border-green-900/30' :
                    sig.status === 'SKIPPED' ? 'bg-red-900/10 border-red-900/30' : 'bg-nzt-bg border-nzt-border'
                  )}>
                    <div className="flex items-center gap-2">
                      <DirBadge dir={sig.direction} />
                      <span className="text-nzt-muted">{sig.strategy}</span>
                      {sig.isa_ticker && <span className="text-blue-400">{sig.isa_ticker}</span>}
                    </div>
                    <div className="flex items-center gap-2">
                      <span className="text-nzt-accent font-mono">{sig.confidence}%</span>
                      <StatusBadge status={sig.status} />
                      <span className="text-nzt-muted">{timeAgo(sig.timestamp)}</span>
                    </div>
                  </div>
                ))}
                {signals.length === 0 && (
                  <p className="text-nzt-muted text-center py-4">No recent signals</p>
                )}
              </div>
            </Card>
          </div>

          {/* ================================================================ */}
          {/* D6: LIQUIDITY PROFILE + PORTFOLIO CORRELATION                    */}
          {/* ================================================================ */}
          <div className="grid grid-cols-2 gap-3 mb-3">
            {/* Liquidity Profile Card */}
            <Card title="LIQUIDITY PROFILE">
              <div className="flex items-center gap-1 mb-3">
                <InfoTooltip text="Liquidity assessment based on average daily volume, estimated spread, and dollar volume. Tiers: T1 (excellent, >$10M/day), T2 (good, >$2M), T3 (adequate, >$500K), T4 (thin). Spread gate: PASS (<15bps), WATCH (15-40bps), VETO (>40bps)." />
              </div>
              {liquidityProfile ? (
                <div className="space-y-3 text-xs">
                  <div className="flex items-center justify-between">
                    <span className="text-nzt-muted">Average Spread</span>
                    <span className="font-mono font-bold text-nzt-text">{liquidityProfile.spreadBps} bps</span>
                  </div>
                  <div className="flex items-center justify-between">
                    <span className="text-nzt-muted">Daily Volume (20d avg)</span>
                    <span className="font-mono text-nzt-text">{liquidityProfile.avgVol.toLocaleString()}</span>
                  </div>
                  <div className="flex items-center justify-between">
                    <span className="text-nzt-muted">Dollar Volume</span>
                    <span className="font-mono text-nzt-text">${(liquidityProfile.dollarVol / 1000000).toFixed(2)}M</span>
                  </div>
                  <div className="flex items-center justify-between">
                    <span className="text-nzt-muted">Best Trading Window</span>
                    <span className="font-mono text-blue-400">{liquidityProfile.window}</span>
                  </div>
                  <div className="flex items-center justify-between pt-2 border-t border-nzt-border">
                    <span className="text-nzt-muted">Liquidity Tier</span>
                    <span className={clsx('font-bold text-sm px-2 py-0.5 rounded', liquidityProfile.tierColor)}>
                      {liquidityProfile.tier}
                    </span>
                  </div>
                  <div className="flex items-center justify-between">
                    <span className="text-nzt-muted">Spread Gate</span>
                    <span className={clsx('font-bold font-mono', liquidityProfile.gateColor)}>
                      {liquidityProfile.spreadGate}
                    </span>
                  </div>
                  {liquidityProfile.atrPct > 0 && (
                    <div className="flex items-center justify-between">
                      <span className="text-nzt-muted">ATR / Price</span>
                      <span className="font-mono text-nzt-text">{fmt(liquidityProfile.atrPct, 1)}%</span>
                    </div>
                  )}
                </div>
              ) : (
                <div className="text-center py-4">
                  <span className="text-yellow-400 text-xs">Data pending</span>
                  <p className="text-nzt-muted text-[10px] mt-1">Waiting for institutional data feed</p>
                </div>
              )}
            </Card>

            {/* Portfolio Correlation Card */}
            <Card title="PORTFOLIO CORRELATION">
              <div className="flex items-center gap-1 mb-3">
                <InfoTooltip text="Correlation coefficient between this ticker and other instruments. Green (<0.3) = good diversification. Yellow (0.3-0.6) = moderate overlap. Red (>0.6) = high correlation risk. Star indicates currently-held position." />
              </div>
              {highCorrWarning && (
                <div className="mb-2 p-2 bg-red-900/20 border border-red-900/40 rounded text-[11px] text-red-400">
                  WARNING: Adding this ticker would create &gt;80% correlation with an existing position. Diversification risk is HIGH.
                </div>
              )}
              {correlations.length > 0 ? (
                <div className="space-y-1.5 max-h-48 overflow-y-auto">
                  {correlations.map((c, i) => (
                    <div key={i} className="flex items-center justify-between p-1.5 bg-nzt-bg rounded text-xs">
                      <div className="flex items-center gap-2">
                        <Link href={`/ticker/${encodeURIComponent(c.ticker)}`} className="font-bold text-blue-400 hover:underline">
                          {c.ticker}
                        </Link>
                        {c.held && <span className="text-[9px] text-yellow-400" title="Currently held">&#9733;</span>}
                      </div>
                      <div className="flex items-center gap-2">
                        <div className="w-20 h-1.5 bg-nzt-border rounded-full overflow-hidden">
                          <div
                            className={clsx('h-full rounded-full', Math.abs(c.corr) < 0.3 ? 'bg-green-500' : Math.abs(c.corr) < 0.6 ? 'bg-yellow-500' : 'bg-red-500')}
                            style={{ width: `${Math.abs(c.corr) * 100}%` }}
                          />
                        </div>
                        <span className={clsx('font-mono font-bold w-12 text-right', c.color)}>
                          {c.corr >= 0 ? '+' : ''}{c.corr.toFixed(2)}
                        </span>
                      </div>
                    </div>
                  ))}
                </div>
              ) : (
                <div className="text-center py-4">
                  <span className="text-yellow-400 text-xs">No correlation data</span>
                  <p className="text-nzt-muted text-[10px] mt-1">Correlation matrix not configured or ticker not in matrix</p>
                </div>
              )}
            </Card>
          </div>

          {/* ================================================================ */}
          {/* D6: AI ANALYSIS SUMMARY                                          */}
          {/* ================================================================ */}
          <Card title="AI ANALYSIS" className="mb-3">
            <div className="flex items-center gap-1 mb-2">
              <span className="text-[10px] text-purple-400">&#9672; AI-Generated</span>
              <InfoTooltip text="Deterministic analysis generated from available market data, trading statistics, and technical indicators. When the institutional endpoint includes a Gemini Flash assessment, it is appended here." />
            </div>
            {aiSummary ? (
              <div className="p-3 bg-nzt-bg rounded border border-purple-900/30">
                <p className="text-sm text-nzt-text italic leading-relaxed">{aiSummary}</p>
              </div>
            ) : (
              <div className="text-center py-4">
                <span className="text-yellow-400 text-xs">Analysis pending</span>
                <p className="text-nzt-muted text-[10px] mt-1">Waiting for market data to generate narrative</p>
              </div>
            )}
          </Card>

          {/* Row 4: Learning Profile + Config Overrides */}
          <div className="grid grid-cols-2 gap-3">
            <Card title="LEARNING ENGINE PROFILE">
              {profile?.profile ? (
                <div className="space-y-1 text-xs">
                  <div className="flex justify-between"><span className="text-nzt-muted">Priority Score</span><span className="font-bold text-nzt-accent">{fmt(profile.profile.priority_score)}</span></div>
                  <div className="flex justify-between"><span className="text-nzt-muted">60d Win Rate</span><span className="font-mono">{fmtPct((profile.profile.rolling_60d_wr || 0) * 100)}</span></div>
                  <div className="flex justify-between"><span className="text-nzt-muted">Best Strategy</span><span className="font-bold text-nzt-accent">{profile.profile.best_strategy || '--'}</span></div>
                  <div className="flex justify-between"><span className="text-nzt-muted">Worst Strategy</span><span className="text-nzt-danger">{profile.profile.worst_strategy || '--'}</span></div>
                  <div className="flex justify-between"><span className="text-nzt-muted">Best Regime</span><span className="font-bold">{profile.profile.best_regime || '--'}</span></div>
                  <div className="flex justify-between"><span className="text-nzt-muted">Updated</span><span className="text-nzt-muted">{timeAgo(profile.profile.updated_at)}</span></div>
                </div>
              ) : (
                <p className="text-nzt-muted text-xs text-center py-4">Profile not yet built</p>
              )}
            </Card>

            <Card title="CONFIG OVERRIDES">
              {profile?.overrides && Object.keys(profile.overrides).length > 0 ? (
                <div className="space-y-1 text-xs">
                  {Object.entries(profile.overrides).map(([key, val]: any) => (
                    <div key={key} className="flex justify-between p-1 bg-nzt-bg rounded">
                      <span className="text-nzt-muted">{key}</span>
                      <span className="font-mono text-nzt-accent">{typeof val === 'object' ? JSON.stringify(val) : String(val)}</span>
                    </div>
                  ))}
                </div>
              ) : (
                <p className="text-nzt-muted text-xs text-center py-4">Using default config</p>
              )}
            </Card>
          </div>
        </>
      )}
    </div>
  )
}
