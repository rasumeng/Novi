import { useState } from 'react'
import { CheckCircle2, Download, Loader2, RefreshCw, Sparkles } from 'lucide-react'
import type { DiscoveryPayload, DiscoveredModelEntry } from './api'
import { LoadingSkeleton } from '@/components/common/LoadingSkeleton'

interface Props {
  discovery: DiscoveryPayload | null
  installing: Record<string, { phase: string; pct: number | null }>
  onInstall: (name: string) => Promise<boolean>
  onRefresh: () => Promise<void>
  loading: boolean
}

/** Models — what's on the machine. Dynamic discovery + install + recommendations. */
export function ModelsSettings({ discovery, installing, onInstall, onRefresh, loading }: Props) {
  const [query, setQuery] = useState('')
  const [refreshing, setRefreshing] = useState(false)

  if (loading || !discovery) return <LoadingSkeleton rows={4} compact />

  const refresh = async () => {
    setRefreshing(true)
    await onRefresh()
    setRefreshing(false)
  }

  const rows = discovery.models
    .filter((m) => m.name.toLowerCase().includes(query.trim().toLowerCase()))
    .sort((a, b) => {
      const rank = { installed: 0, missing: 1, available: 2 } as Record<string, number>
      return (rank[a.status] ?? 3) - (rank[b.status] ?? 3)
    })

  const missingCount = discovery.missingModels.length
  const installedCount = discovery.models.filter((m) => m.status === 'installed').length

  return (
    <div className="space-y-5">
      <div>
        <p className="text-sm text-base-100 font-medium mb-1">Model library</p>
        <p className="text-xs text-base-500 mb-3">
          Live inventory of every model available on this machine. Install from here — changes apply immediately.
        </p>
        <div className="flex items-center gap-2 mb-3">
          <input
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            placeholder="Filter by name…"
            className="flex-1 bg-base-900 border border-base-700 rounded-lg px-3 py-1.5 text-xs text-base-200 outline-none focus:border-accent/40"
          />
          <button
            onClick={refresh}
            disabled={refreshing}
            className="p-2 rounded-lg border border-base-700 text-base-400 hover:text-base-200 transition-colors disabled:opacity-50"
            title="Rescan"
          >
            <RefreshCw size={14} className={refreshing ? 'animate-spin' : ''} />
          </button>
        </div>
        <div className="flex items-center gap-3 text-[11px] text-base-500 mb-3">
          <span className="flex items-center gap-1"><span className="w-2 h-2 rounded-full bg-emerald-500" /> {installedCount} installed</span>
          {missingCount > 0 && (
            <span className="flex items-center gap-1"><span className="w-2 h-2 rounded-full bg-amber-500" /> {missingCount} missing</span>
          )}
        </div>
      </div>

      <div className="space-y-2">
        {rows.map((m) => (
          <ModelRow key={m.name} model={m} install={installing[m.name]} onInstall={onInstall} />
        ))}
        {rows.length === 0 && (
          <p className="text-xs text-base-500 py-6 text-center">
            {query ? `No models match "${query}".` : 'No models detected. Install one below.'}
          </p>
        )}
      </div>
    </div>
  )
}

function ModelRow({ model, install, onInstall }: {
  model: DiscoveredModelEntry
  install?: { phase: string; pct: number | null }
  onInstall: (name: string) => Promise<boolean>
}) {
  const installed = model.status === 'installed'
  const busy = install && install.phase !== 'done'
  return (
    <div className="p-3 rounded-xl bg-base-800/50 border border-base-700 flex items-center justify-between gap-3">
      <div className="min-w-0 flex-1">
        <div className="flex items-center gap-2">
          <p className="text-sm text-base-100 font-mono truncate">{model.displayName}</p>
          <StatusBadge status={model.status} />
        </div>
        {model.reasons.length > 0 && (
          <p className="flex items-center gap-1 text-[11px] text-accent mt-1">
            <Sparkles size={11} /> {model.reasons.join(' · ')}
          </p>
        )}
        {install && (
          <p className="text-[11px] text-base-500 mt-1">
            {install.phase === 'done' ? 'Install complete' : `${install.phase}${install.pct != null ? ` — ${install.pct}%` : ''}`}
          </p>
        )}
      </div>
      {!installed && (
        <button
          onClick={() => void onInstall(model.name)}
          disabled={busy}
          className="flex items-center gap-1.5 px-3 py-1.5 text-xs font-medium rounded-lg bg-accent text-white hover:bg-accent/90 transition-colors disabled:opacity-60 shrink-0"
        >
          {busy ? <Loader2 size={13} className="animate-spin" /> : <Download size={13} />}
          {busy ? (install.phase === 'done' ? 'Installed' : 'Installing…') : 'Install'}
        </button>
      )}
    </div>
  )
}

function StatusBadge({ status }: { status: DiscoveredModelEntry['status'] }) {
  if (status === 'installed')
    return (
      <span className="flex items-center gap-1 px-1.5 py-0.5 rounded bg-emerald-500/10 border border-emerald-500/20 text-[10px] text-emerald-400">
        <CheckCircle2 size={10} /> installed
      </span>
    )
  if (status === 'available')
    return (
      <span className="flex items-center gap-1 px-1.5 py-0.5 rounded bg-sky-500/10 border border-sky-500/20 text-[10px] text-sky-400">
        available
      </span>
    )
  return (
    <span className="flex items-center gap-1 px-1.5 py-0.5 rounded bg-amber-500/10 border border-amber-500/20 text-[10px] text-amber-400">
      missing
    </span>
  )
}