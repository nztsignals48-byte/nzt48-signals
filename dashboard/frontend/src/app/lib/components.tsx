'use client'

import Link from 'next/link'
import clsx from 'clsx'

// === Card ===
export function Card({ title, children, className = '' }: { title: string; children: React.ReactNode; className?: string }) {
  return (
    <div className={clsx('bg-nzt-card rounded-lg border border-nzt-border p-3', className)}>
      <h3 className="text-[10px] font-semibold text-nzt-muted uppercase tracking-wider mb-2">{title}</h3>
      {children}
    </div>
  )
}

// === Direction Badge ===
export function DirBadge({ dir }: { dir: string }) {
  return (
    <span className={clsx('text-[10px] font-bold px-1 py-0.5 rounded',
      dir === 'LONG' ? 'bg-green-900/50 text-green-400' : 'bg-red-900/50 text-red-400'
    )}>{dir}</span>
  )
}

// === Status Badge ===
export function StatusBadge({ status }: { status: string }) {
  return (
    <span className={clsx('text-[10px] px-1 rounded font-bold',
      status === 'TAKEN' ? 'text-green-400' : status === 'SKIPPED' ? 'text-red-400' : 'text-yellow-400'
    )}>{status}</span>
  )
}

// === Bot Badge ===
export function BotBadge({ bot }: { bot: string }) {
  return (
    <span className={clsx('text-[10px] font-bold px-1 py-0.5 rounded',
      bot === 'A' ? 'bg-blue-900/50 text-blue-400' : 'bg-purple-900/50 text-purple-400'
    )}>
      {bot === 'A' ? 'ISA' : 'US'}
    </span>
  )
}

// === Clickable Ticker Link ===
export function TickerLink({ ticker, className }: { ticker: string; className?: string }) {
  return (
    <Link
      href={`/ticker/${encodeURIComponent(ticker)}`}
      className={clsx('font-bold hover:text-nzt-accent hover:underline transition cursor-pointer', className)}
    >
      {ticker}
    </Link>
  )
}

// === Scan Health Badge ===
export function ScanHealthBadge({ state }: { state: string }) {
  return (
    <span className={clsx('text-[10px] font-bold px-1.5 py-0.5 rounded',
      state === 'OK' ? 'bg-green-900/50 text-green-400' :
      state === 'DEGRADED' ? 'bg-yellow-900/50 text-yellow-400' :
      state === 'HALTED' ? 'bg-red-900/50 text-red-400' :
      'bg-nzt-border text-nzt-muted'
    )}>{state}</span>
  )
}

// === Exit Intent Badge ===
export function ExitIntentBadge({ intent }: { intent: string }) {
  return (
    <span className={clsx('text-[10px] font-bold px-1.5 py-0.5 rounded',
      intent === 'HOLD' ? 'bg-green-900/50 text-green-400' :
      intent === 'TRAIL' ? 'bg-yellow-900/50 text-yellow-400' :
      intent === 'PARTIAL' ? 'bg-orange-900/50 text-orange-400' :
      intent === 'EXIT_NOW' ? 'bg-red-900/50 text-red-400' :
      'bg-nzt-border text-nzt-muted'
    )}>{intent}</span>
  )
}

// === Opportunity Decision Badge ===
export function OpportunityDecisionBadge({ decision }: { decision: string }) {
  return (
    <span className={clsx('text-[10px] font-bold px-1.5 py-0.5 rounded',
      decision === 'TRADE' ? 'bg-green-900/50 text-green-400' :
      decision === 'WATCH' ? 'bg-blue-900/50 text-blue-400' :
      'bg-nzt-border text-nzt-muted'
    )}>{decision}</span>
  )
}

// === Lane Badge — TRADE/WATCH/INTEL/ABSTAIN ===
export function LaneBadge({ lane }: { lane: string }) {
  const cfg: Record<string, { bg: string; text: string; label: string }> = {
    TRADE: { bg: 'bg-green-900/50', text: 'text-green-400', label: 'TRADE' },
    WATCH: { bg: 'bg-amber-900/50', text: 'text-amber-400', label: 'WATCH' },
    INTEL: { bg: 'bg-blue-900/50', text: 'text-blue-400', label: 'INTEL' },
    ABSTAIN: { bg: 'bg-red-900/50', text: 'text-red-400', label: 'ABSTAIN' },
  }
  const c = cfg[lane] || cfg.INTEL
  return (
    <span className={clsx('text-[10px] font-bold px-1.5 py-0.5 rounded', c.bg, c.text)}>
      {c.label}
    </span>
  )
}

// === Score Bar — horizontal fill bar with label ===
export function ScoreBar({ label, value, max = 100, color }: { label: string; value: number; max?: number; color?: string }) {
  const pct = Math.min(Math.max((value / max) * 100, 0), 100)
  const barColor = color || (pct >= 70 ? 'bg-green-500' : pct >= 45 ? 'bg-amber-500' : 'bg-red-500')
  return (
    <div className="flex items-center gap-2 text-[11px]">
      <span className="text-nzt-muted w-20 shrink-0 truncate">{label}</span>
      <div className="flex-1 h-2 bg-nzt-border rounded-full overflow-hidden">
        <div className={clsx('h-full rounded-full transition-all', barColor)} style={{ width: `${pct}%` }} />
      </div>
      <span className="font-mono text-nzt-text w-8 text-right">{Math.round(value)}</span>
    </div>
  )
}

// === Sector Signal Badge — INFLOW/OUTFLOW/NEUTRAL ===
export function SectorSignalBadge({ signal }: { signal: string }) {
  const cfg: Record<string, { bg: string; text: string }> = {
    INFLOW: { bg: 'bg-green-900/50', text: 'text-green-400' },
    OUTFLOW: { bg: 'bg-red-900/50', text: 'text-red-400' },
    RISING: { bg: 'bg-amber-900/50', text: 'text-amber-400' },
    FALLING: { bg: 'bg-orange-900/50', text: 'text-orange-400' },
    NEUTRAL: { bg: 'bg-nzt-border', text: 'text-nzt-muted' },
    LEADER: { bg: 'bg-green-900/50', text: 'text-green-400' },
  }
  const c = cfg[signal] || cfg.NEUTRAL
  return (
    <span className={clsx('text-[10px] font-bold px-1.5 py-0.5 rounded', c.bg, c.text)}>
      {signal}
    </span>
  )
}

// === Navigation Tabs ===
export function NavTabs({ active }: { active?: 'command' | 'analysis' | 'history' | 'portfolio' | 'sectors' | 'gate' }) {
  const tabs = [
    { key: 'command', label: 'Command Center', href: '/' },
    { key: 'analysis', label: 'Data Analysis', href: '/analysis' },
    { key: 'history', label: 'History', href: '/history' },
    { key: 'portfolio', label: 'Portfolio', href: '/portfolio' },
    { key: 'sectors', label: 'Sectors', href: '/sectors' },
    { key: 'gate', label: '⚡ Go-Live Gate', href: '/gate' },
  ] as const

  return (
    <nav className="flex gap-1">
      {tabs.map(t =>
        t.key === active ? (
          <span key={t.key} className="px-4 py-2 text-sm rounded border border-nzt-accent/50 text-nzt-accent bg-nzt-accent/10">
            {t.label}
          </span>
        ) : (
          <Link key={t.key} href={t.href} className="px-4 py-2 text-sm rounded border border-nzt-border text-nzt-muted hover:text-nzt-text hover:border-nzt-accent/50 transition">
            {t.label}
          </Link>
        )
      )}
    </nav>
  )
}
