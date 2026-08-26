import { useEffect, useState } from 'react'
import { fetchKnowledgeOverview } from '@/services/novi'
import type { KnowledgeOverview as KnowledgeOverviewData } from '@/types'

/**
 * PLACEHOLDER — RECONSTRUCTED during the Cozmo → Novi rebrand.
 *
 * The original component was an untracked file lost in the rename; this
 * version was rebuilt from the data contract (KnowledgeOverview types,
 * fetchKnowledgeOverview, MemorySettings usage) and may differ from the
 * original. Review before relying on it.
 *
 * "What Novi knows" — grouped, human-readable projection of the Brain's
 * knowledge store. Read-only: this tab is for transparency, not editing.
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
    return <p className="text-xs text-base-500">Loading what Novi knows...</p>
  }

  const categories = overview?.categories ?? []
  if (categories.length === 0) {
    return (
      <p className="text-xs text-base-500">
        Nothing learned yet. As you chat, facts, preferences, and goals Novi
        extracts will appear here.
      </p>
    )
  }

  return (
    <div className="space-y-4">
      <p className="text-[11px] text-base-500">
        {overview?.total ?? 0} knowledge entries · updated{' '}
        {overview?.updated ? new Date(overview.updated).toLocaleString() : '—'}
      </p>
      {categories.map((cat) => (
        <div key={cat.category || cat.label} className="space-y-1.5">
          <p className="text-[11px] font-semibold uppercase tracking-wide text-base-400">
            {cat.label}
          </p>
          <ul className="space-y-1">
            {cat.entries.map((entry, i) => (
              <li
                key={i}
                className="text-xs text-base-300 bg-base-800/60 border border-base-700/60 rounded-md px-2.5 py-1.5"
              >
                {entry.content}
              </li>
            ))}
          </ul>
        </div>
      ))}
    </div>
  )
}
