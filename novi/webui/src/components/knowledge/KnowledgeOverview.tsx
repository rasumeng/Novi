import { useEffect, useState } from 'react'
import { fetchKnowledgeOverview } from '@/services/novi'
import type { KnowledgeOverview as KnowledgeOverviewData } from '@/types'

/**
 * What Novi noticed — human-facing projection of memory.
 * Read-only: transparency, not editing. Groups by what matters to the user.
 */
export function KnowledgeOverview() {
  const [overview, setOverview] = useState<KnowledgeOverviewData | null>(null)
  const [loading, setLoading] = useState(true)

  useEffect(() => {
    let alive = true
    fetchKnowledgeOverview()
      .then((data) => { if (alive) setOverview(data) })
      .catch(() => {})
      .finally(() => { if (alive) setLoading(false) })
    return () => { alive = false }
  }, [])

  if (loading) {
    return <p className="text-xs text-base-500">Loading what Novi noticed…</p>
  }

  const categories = overview?.categories ?? []
  if (categories.length === 0) {
    return (
      <div className="rounded-xl border border-base-700/40 bg-base-900/30 px-4 py-6 text-center">
        <p className="text-sm font-medium text-base-300">Novi hasn't learned anything yet</p>
        <p className="text-xs text-base-500 mt-1 leading-relaxed">It will notice preferences and facts as you chat — they will appear here with where they came from and when.</p>
      </div>
    )
  }

  return (
    <div className="space-y-5">
      <p className="text-[11px] text-base-500">
        {overview?.total ?? 0} things Novi noticed · updated{' '}
        {overview?.updated ? new Date(overview.updated).toLocaleString() : '—'}
      </p>
      {categories.map((cat) => (
        <div key={cat.category || cat.label} className="space-y-2">
          <p className="text-[11px] font-semibold tracking-widest uppercase text-base-500">
            {cat.label}
          </p>
          <ul className="space-y-1.5">
            {cat.entries.map((entry, i) => (
              <li
                key={i}
                className="text-[13px] leading-relaxed text-base-200 bg-base-900 border border-base-800 rounded-xl px-3 py-2.5"
              >
                {entry.content}
                {entry.evidence && <span className="block text-[11px] text-base-500 mt-1">Source: {entry.evidence}</span>}
              </li>
            ))}
          </ul>
        </div>
      ))}
    </div>
  )
}
