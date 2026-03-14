// === Shared API Helpers ===

export function getAPI(): string {
  if (typeof window !== 'undefined') {
    // Client-side: always derive from current hostname so it works from any IP/domain
    return `http://${window.location.hostname}:8000`
  }
  // Server-side (SSR): use internal Docker hostname or env var
  return process.env.API_URL || 'http://localhost:8000'
}

export function getWSURL(): string {
  return getAPI().replace(/^http/, 'ws') + '/ws/live'
}

export async function fetchAPI<T>(path: string): Promise<T> {
  const res = await fetch(`${getAPI()}${path}`)
  if (!res.ok) throw new Error(`API error: ${res.status}`)
  return res.json()
}

export function fmt(n: any, d: number = 2): string {
  const v = Number(n)
  return isNaN(v) ? '0' : v.toFixed(d)
}

export function fmtDollar(n: any): string {
  const v = Number(n)
  if (isNaN(v)) return '$0.00'
  return '$' + v.toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 })
}

export function fmtPct(n: any): string {
  return fmt(n, 1) + '%'
}

export function timeAgo(ts: string | null | undefined): string {
  if (!ts) return '--'
  const diff = Date.now() - new Date(ts).getTime()
  if (diff < 60000) return `${Math.floor(diff / 1000)}s ago`
  if (diff < 3600000) return `${Math.floor(diff / 60000)}m ago`
  if (diff < 86400000) return `${Math.floor(diff / 3600000)}h ago`
  return `${Math.floor(diff / 86400000)}d ago`
}

export function fmtDate(ts: string | null | undefined): string {
  if (!ts) return '--'
  return new Date(ts).toLocaleDateString('en-GB', { day: '2-digit', month: 'short', year: '2-digit' })
}

export function fmtTime(ts: string | null | undefined): string {
  if (!ts) return '--'
  return new Date(ts).toLocaleTimeString('en-GB', { hour: '2-digit', minute: '2-digit' })
}
