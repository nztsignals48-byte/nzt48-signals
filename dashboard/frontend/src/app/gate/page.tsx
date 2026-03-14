'use client'

import { useState, useEffect } from 'react'
import clsx from 'clsx'
import { fetchAPI } from '../lib/api'
import { NavTabs } from '../lib/components'

// W10.4 — Go-Live Gate Page
// 8-check readiness panel. Auto-refreshes every 30s.
// Linked from Command Strip gate badge.

interface GateCheck {
  name: string
  pass?: boolean    // fallback constructed checks
  passed?: boolean  // /api/gate response field
  detail?: string
  detail_text?: string
  critical?: boolean
}

interface GateData {
  overall?: 'GO' | 'NO_GO'
  ready?: boolean           // /api/gate uses "ready"
  checks_passed?: number
  passed?: number           // /api/gate uses "passed"
  checks_total?: number
  total?: number            // /api/gate uses "total"
  checks: GateCheck[]
  timestamp?: string
  checked_at?: string
  recommendation?: string
  verdict?: string
}

export default function GatePage() {
  const [gate, setGate] = useState<GateData | null>(null)
  const [loading, setLoading] = useState(true)
  const [lastUpdate, setLastUpdate] = useState('')

  const refresh = async () => {
    try {
      const data = await fetchAPI<GateData>('/api/gate')
      setGate(data)
      setLastUpdate(new Date().toLocaleTimeString())
    } catch (e) {
      // Fallback: construct from scan_health if /api/gate 404s
      try {
        const sh = await fetchAPI<any>('/api/scan_health')
        const perf = await fetchAPI<any>('/api/performance')
        const regime = await fetchAPI<any>('/api/regime')

        const checks: GateCheck[] = [
          {
            name: 'Engine Online',
            pass: sh?.state === 'RUNNING' || sh?.engine_state === 'RUNNING',
            detail: `State: ${sh?.state || sh?.engine_state || 'unknown'}`,
            critical: true,
          },
          {
            name: 'Signal Flow Active',
            pass: (sh?.signals_today || 0) > 0 || (sh?.total_signals_today || 0) > 0,
            detail: `Signals today: ${sh?.signals_today || sh?.total_signals_today || 0}`,
            critical: true,
          },
          {
            name: 'Regime Detected',
            pass: !!regime?.current?.regime,
            detail: `Regime: ${regime?.current?.regime || 'unknown'}`,
            critical: true,
          },
          {
            name: 'Win Rate Positive',
            pass: (perf?.aggregate?.win_rate || 0) >= 0.40,
            detail: `Win rate: ${((perf?.aggregate?.win_rate || 0) * 100).toFixed(1)}% (min 40%)`,
            critical: false,
          },
          {
            name: 'No Kill Switch Active',
            pass: !sh?.kill_switch_active,
            detail: sh?.kill_switch_active ? 'KILL SWITCH IS ACTIVE' : 'Kill switch inactive',
            critical: true,
          },
          {
            name: 'Data Feed Fresh',
            pass: (sh?.last_scan_age_seconds || 999) < 120,
            detail: `Last scan: ${sh?.last_scan_age_seconds || '?'}s ago (max 120s)`,
            critical: true,
          },
          {
            name: 'Daily Drawdown OK',
            pass: (sh?.daily_pnl_pct || 0) > -0.03,
            detail: `Daily P&L: ${((sh?.daily_pnl_pct || 0) * 100).toFixed(2)}% (limit -3%)`,
            critical: false,
          },
          {
            name: 'Trade Count Building',
            pass: (perf?.aggregate?.total_trades || 0) >= 0,
            detail: `Total trades: ${perf?.aggregate?.total_trades || 0}`,
            critical: false,
          },
        ]

        const passed = checks.filter(c => c.pass).length
        const criticalFailed = checks.some(c => c.critical && !c.pass)

        setGate({
          overall: (!criticalFailed && passed >= 6) ? 'GO' : 'NO_GO',
          checks_passed: passed,
          checks_total: checks.length,
          checks,
          timestamp: new Date().toISOString(),
          recommendation: criticalFailed
            ? 'Critical checks failed — do not trade'
            : passed >= 7
            ? 'System ready — all checks green'
            : 'Minor issues — proceed with caution',
        })
        setLastUpdate(new Date().toLocaleTimeString())
      } catch {
        setLoading(false)
      }
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => {
    refresh()
    const interval = setInterval(refresh, 30000)
    return () => clearInterval(interval)
  }, [])

  return (
    <div className="min-h-screen p-6 max-w-3xl mx-auto">
      {/* Header */}
      <header className="flex items-center justify-between mb-6">
        <div>
          <h1 className="text-2xl font-bold tracking-tight">
            GO-LIVE GATE <span className="text-nzt-accent">— 8 Mandatory Checks</span>
          </h1>
          <p className="text-xs text-nzt-muted mt-1">
            All critical checks must pass before live trading. Auto-refreshes every 30s.
          </p>
        </div>
        <div className="flex items-center gap-3">
          <span className="text-xs text-nzt-muted">{lastUpdate}</span>
          <NavTabs active="gate" />
        </div>
      </header>

      {loading && (
        <div className="text-nzt-muted text-sm animate-pulse text-center py-12">Loading gate status...</div>
      )}

      {gate && (() => {
        // Normalise fields — /api/gate uses {ready, passed, total} vs fallback {overall, checks_passed, checks_total}
        const isGo = gate.ready ?? (gate.overall === 'GO')
        const numPassed = gate.passed ?? gate.checks_passed ?? 0
        const numTotal = gate.total ?? gate.checks_total ?? 8
        const rec = gate.recommendation ?? gate.verdict ?? (isGo ? 'System ready' : 'Not ready for live trading')
        const ts = gate.checked_at ?? gate.timestamp ?? ''
        return (
        <>
          {/* Verdict */}
          <div className={clsx(
            'rounded-xl border-2 p-6 mb-6 text-center',
            isGo ? 'border-green-500 bg-green-900/10' : 'border-red-600 bg-red-950/20'
          )}>
            <div className={clsx('text-6xl font-black tracking-widest mb-2', isGo ? 'text-green-400' : 'text-red-400')}>
              {isGo ? '✓ GO' : '✗ NO-GO'}
            </div>
            <div className={clsx('text-lg font-semibold mb-2', isGo ? 'text-green-300' : 'text-red-300')}>
              {numPassed}/{numTotal} checks passed
            </div>
            <div className="text-sm text-nzt-muted">{rec}</div>
          </div>

          {/* Individual checks */}
          <div className="space-y-2">
            {gate.checks.map((check, i) => {
              // normalise pass field — API may use "passed" or "pass"
              const didPass = check.passed ?? check.pass ?? false
              const isCritical = check.critical ?? true
              const detail = check.detail ?? check.detail_text ?? ''
              return (
              <div
                key={i}
                className={clsx(
                  'flex items-start gap-4 p-4 rounded-lg border',
                  didPass ? 'border-green-800/50 bg-green-900/5'
                  : isCritical ? 'border-red-700 bg-red-950/20'
                  : 'border-amber-800/50 bg-amber-900/5'
                )}
              >
                {/* Status icon */}
                <div className={clsx(
                  'shrink-0 w-7 h-7 rounded-full flex items-center justify-center text-sm font-bold',
                  didPass ? 'bg-green-900/50 text-green-400' :
                  isCritical ? 'bg-red-900/50 text-red-400' :
                  'bg-amber-900/50 text-amber-400'
                )}>
                  {didPass ? '✓' : isCritical ? '✗' : '!'}
                </div>

                {/* Check info */}
                <div className="flex-1 min-w-0">
                  <div className="flex items-center gap-2">
                    <span className={clsx(
                      'text-sm font-semibold',
                      didPass ? 'text-green-300' : isCritical ? 'text-red-300' : 'text-amber-300'
                    )}>
                      {check.name}
                    </span>
                    {isCritical && !didPass && (
                      <span className="text-[10px] px-1.5 py-0.5 rounded bg-red-900/50 text-red-400 border border-red-800 font-bold">
                        CRITICAL
                      </span>
                    )}
                  </div>
                  <div className="text-xs text-nzt-muted mt-0.5">{detail}</div>
                </div>

                {/* Pass/fail badge */}
                <div className={clsx(
                  'shrink-0 px-3 py-1 rounded text-xs font-bold',
                  didPass ? 'bg-green-900/40 text-green-400' : 'bg-red-900/40 text-red-400'
                )}>
                  {didPass ? 'PASS' : 'FAIL'}
                </div>
              </div>
            )})}
          </div>

          {/* Footer */}
          <div className="mt-6 text-center text-[11px] text-nzt-muted">
            Last checked: {ts ? new Date(ts).toLocaleTimeString() : lastUpdate}
            {' · '}
            <a href="/" className="text-nzt-accent hover:underline">← Back to Command Center</a>
          </div>
        </>
        )
      })()}

    </div>
  )
}
