// === Shared Color Helpers ===

export function getRegimeColor(regime: string | undefined): string {
  if (!regime) return 'text-nzt-muted'
  if (regime.includes('UP_STRONG')) return 'text-green-400'
  if (regime.includes('UP_MOD')) return 'text-green-300'
  if (regime.includes('DOWN_STRONG')) return 'text-red-400'
  if (regime.includes('DOWN_MOD')) return 'text-red-300'
  if (regime.includes('RANGE')) return 'text-yellow-400'
  if (regime.includes('HIGH_VOL')) return 'text-orange-400'
  if (regime.includes('RISK_OFF')) return 'text-red-500'
  if (regime.includes('SHOCK')) return 'text-red-600'
  return 'text-nzt-muted'
}

export function getDDColor(level: string | undefined): string {
  if (!level) return 'text-nzt-accent'
  if (level === 'GREEN') return 'text-nzt-accent'
  if (level === 'YELLOW') return 'text-yellow-400'
  if (level === 'ORANGE') return 'text-orange-400'
  if (level === 'RED') return 'text-red-400'
  if (level === 'CRITICAL') return 'text-red-500'
  if (level === 'EMERGENCY') return 'text-red-600'
  return 'text-nzt-muted'
}

export function getGradeColor(grade: string | undefined): string {
  if (!grade) return 'bg-nzt-border text-nzt-muted'
  if (grade === 'A' || grade === 'A+') return 'bg-green-900/30 text-green-400'
  if (grade === 'B' || grade === 'B+') return 'bg-blue-900/30 text-blue-400'
  if (grade === 'C') return 'bg-yellow-900/30 text-yellow-400'
  if (grade === 'D') return 'bg-orange-900/30 text-orange-400'
  if (grade === 'F') return 'bg-red-900/30 text-red-400'
  return 'bg-nzt-border text-nzt-muted'
}

export function getVixColor(vix: number): string {
  if (vix < 15) return 'text-nzt-accent'
  if (vix < 25) return 'text-yellow-400'
  if (vix < 35) return 'text-orange-400'
  return 'text-nzt-danger'
}

export function getVixLabel(vix: number): string {
  if (vix === 0) return 'No data'
  if (vix < 15) return 'Low Volatility'
  if (vix < 25) return 'Normal'
  if (vix < 35) return 'Elevated'
  return 'Extreme Fear'
}

export function getHeatmapBg(changePct: number): string {
  if (changePct <= -3) return 'bg-red-900/60'
  if (changePct <= -1) return 'bg-red-900/30'
  if (changePct >= 3) return 'bg-green-900/60'
  if (changePct >= 1) return 'bg-green-900/30'
  return 'bg-nzt-bg'
}

export function getRiskBudgetColor(used: number, total: number): string {
  const pct = total > 0 ? (used / total) * 100 : 0
  if (pct < 50) return 'bg-nzt-accent'
  if (pct < 75) return 'bg-nzt-warning'
  return 'bg-nzt-danger'
}

// === Lane Colors ===
export function getLaneBorder(lane: string): string {
  if (lane === 'TRADE') return 'border-green-500/50'
  if (lane === 'WATCH') return 'border-amber-500/50'
  if (lane === 'INTEL') return 'border-blue-500/50'
  if (lane === 'ABSTAIN') return 'border-red-500/50'
  return 'border-nzt-border'
}

export function getLaneBg(lane: string): string {
  if (lane === 'TRADE') return 'bg-green-900/10'
  if (lane === 'WATCH') return 'bg-amber-900/10'
  if (lane === 'INTEL') return 'bg-blue-900/10'
  if (lane === 'ABSTAIN') return 'bg-red-900/10'
  return 'bg-nzt-card'
}

// === Score Color ===
export function getScoreColor(score: number): string {
  if (score >= 65) return 'text-green-400'
  if (score >= 50) return 'text-amber-400'
  return 'text-red-400'
}

export function getScoreBorder(score: number): string {
  if (score >= 65) return 'border-green-500'
  if (score >= 50) return 'border-amber-500'
  return 'border-red-500'
}

export function getScoreBg(score: number): string {
  if (score >= 65) return 'bg-green-900/20'
  if (score >= 50) return 'bg-amber-900/20'
  return 'bg-red-900/20'
}
