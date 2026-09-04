import { useEffect, useState, useCallback } from 'react'
import { fetchKnowledgeOverview } from '@/services/novi'
import type { KnowledgeOverview as KnowledgeOverviewData } from '@/types'
import { LoadingSkeleton } from '@/components/common/LoadingSkeleton'
import { EmptyState } from '@/components/common/EmptyState'
import { Brain, RefreshCw } from 'lucide-react'

/**
 * What Novi noticed — human-facing projection of memory.
 * Read-only: transparency, not editing. Groups by what matters to the user.
 */
export function KnowledgeOverview() {
  const [overview, setOverview] = useState<any | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)

  const load = useCallback(() => {
    setLoading(true)
    setError(null)
    fetchKnowledgeOverview()
      .then((data: any) => {
        if (data && data.brainAvailable === false) {
          setError(data.error || 'Brain store unavailable — check logs')
        } else {
          setError(null)
        }
        setOverview(data)
      })
      .catch(() => setError('Brain store unavailable — check logs'))
      .finally(() => setLoading(false))
  }, [])

  useEffect(() => {
    load()
  }, [load])

  if (loading) {
    return <LoadingSkeleton rows={5} compact />
  }

  if (error) {
    return (
      <div className="rounded-xl border border-amber-500/30 bg-amber-500/10 px-4 py-6 text-center">
        <p className="text-sm font-medium text-amber-300">Brain store unavailable — check logs</p>
        <p className="text-xs text-base-500 mt-1">{error}</p>
        <button onClick={load} className="mt-3 inline-flex items-center gap-1.5 px-3 py-1.5 rounded-full bg-base-800 border border-base-700 text-xs text-base-300 hover:bg-base-700 transition-colors">
          <RefreshCw size={12} /> Retry
        </button>
      </div>
    )
  }

  const categories: any[] = overview?.categories ?? []
  if (categories.length === 0) {
    return (
      <EmptyState
        icon={Brain}
        title="No knowledge yet — start a conversation"
        description="It will notice preferences and facts as you chat — they will appear here with where they came from and when."
      />
    )
  }

  return (
    <div className="space-y-5">
      <p className="text-[11px] text-base-500">
        {overview?.total ?? 0} things Novi noticed · updated{' '}
        {overview?.updated ? new Date(overview.updated).toLocaleString() : '—'}
      </p>
      {categories.map((cat: any) => (
        <div key={cat.category || cat.label} className="space-y-2">
          <p className="text-[11px] font-semibold tracking-widest uppercase text-base-500">
            {cat.label}
          </p>
          <ul className="space-y-1.5">
            {cat.entries.map((entry: any, i: number) => (
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
