import { TimelineEntry } from '@/types'

// Pure timeline helpers shared by the live feed, TimelinePage refresh, and tests.
// Every row's `id is a per-row instance id (never a Brain id); this key both dedupes
// live-vs-history and gives React a stable key.

function ts(value: string | undefined): number {
  if (!value) return 0
  const t = new Date(value).getTime()
  return Number.isNaN(t) ? 0 : t
}

/** Dedupe by id and sort newest-first. */
export function mergeTimeline(entries: (TimelineEntry | null | undefined)[]): TimelineEntry[] {
  const seen = new Map<string, TimelineEntry>()
  for (const e of entries) {
    if (!e || !e.id) continue
    seen.set(e.id, e)
  }
  return Array.from(seen.values()).sort((a, b) => ts(b.timestamp) - ts(a.timestamp))
}

export interface DayGroup {
  label: 'Today' | 'Yesterday' | string
  /** Newest-first rendered groups are built in descending day order; day = day-stamp for dedupe. */
  day: number
  entries: TimelineEntry[]
}

function startOfDay(d: Date): number {
  const c = new Date(d.getFullYear(), d.getMonth(), d.getDate())
  return c.getTime()
}

/** Group a sorted (newest-first) timeline into day buckets. */
export function groupByDay(entries: TimelineEntry[], now = new Date()): DayGroup[] {
  const today = startOfDay(now)
  const yesterday = today - 86400000
  const buckets: DayGroup[] = []
  let current: DayGroup | null = null
  for (const e of entries) {
    const day = startOfDay(new Date(ts(e.timestamp) || now.getTime()))
    if (!current || current.day !== day) {
      const label: string = day === today ? 'Today' : day === yesterday ? 'Yesterday' : formatDay(e.timestamp)
      current = { label, day, entries: [] }
      buckets.push(current)
    }
    current.entries.push(e)
  }
  return buckets
}

function formatDay(iso: string | undefined): string {
  const d = new Date(ts(iso))
  if (Number.isNaN(d.getTime())) return 'Earlier'
  return d.toLocaleDateString(undefined, { weekday: 'long', month: 'short', day: 'numeric' })
}

export function timelineTime(iso: string): string {
  const d = new Date(ts(iso))
  if (Number.isNaN(d.getTime())) return ''
  return d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })
}