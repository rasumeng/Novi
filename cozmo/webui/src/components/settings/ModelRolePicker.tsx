import { useState } from 'react'
import { ChevronDown } from 'lucide-react'
import type { ModelCapability, ModelCatalogEntry } from '@/product/types'
import { ModelCatalogCard } from './ModelCatalogCard'

interface Props {
  label: string
  description: string
  capability: ModelCapability
  currentModelId: string
  catalog: ModelCatalogEntry[]
  onChange: (modelId: string) => void
}

// A single product-concept model choice — "Conversation Model," "Coding
// Model," etc. Shows what's selected today; "Change" expands a catalog list
// filtered to models that actually have this capability, supported models
// first. This is the primary, beginner/intermediate-safe way to pick a
// model — no role names, no routing architecture.
export function ModelRolePicker({ label, description, capability, currentModelId, catalog, onChange }: Props) {
  const [browsing, setBrowsing] = useState(false)

  const current = catalog.find((m) => m.id === currentModelId)
  const relevant = catalog.filter((m) => m.capabilities.includes(capability))
  const supported = relevant.filter((m) => m.tier === 'supported')
  const experimental = relevant.filter((m) => m.tier === 'experimental')

  return (
    <div>
      <div className="flex items-center justify-between mb-1.5">
        <div>
          <p className="text-sm text-base-100 font-medium">{label}</p>
          <p className="text-xs text-base-500">{description}</p>
        </div>
        <button
          onClick={() => setBrowsing((v) => !v)}
          className="flex items-center gap-1 px-2.5 py-1 rounded-lg text-xs font-medium text-accent hover:bg-accent/10 transition-colors shrink-0"
        >
          {browsing ? 'Close' : 'Change'}
          <ChevronDown size={12} className={`transition-transform ${browsing ? 'rotate-180' : ''}`} />
        </button>
      </div>

      {current ? (
        <ModelCatalogCard entry={current} selected />
      ) : currentModelId ? (
        <div className="p-3 rounded-xl border border-base-700 bg-base-800/40 text-xs text-base-400">
          Using <span className="font-medium text-base-200">{currentModelId}</span> — not in Cozmo's catalog, but still usable.
        </div>
      ) : (
        <div className="p-3 rounded-xl border border-dashed border-base-700 text-xs text-base-500">
          No model chosen yet.
        </div>
      )}

      {browsing && (
        <div className="mt-2 space-y-2 max-h-72 overflow-y-auto pr-1">
          {supported.map((m) => (
            <ModelCatalogCard
              key={m.id}
              entry={m}
              selected={m.id === currentModelId}
              onSelect={() => { onChange(m.id); setBrowsing(false) }}
            />
          ))}
          {experimental.length > 0 && (
            <>
              <p className="text-[10px] font-semibold uppercase tracking-wider text-base-500 pt-1">Experimental</p>
              {experimental.map((m) => (
                <ModelCatalogCard
                  key={m.id}
                  entry={m}
                  selected={m.id === currentModelId}
                  onSelect={() => { onChange(m.id); setBrowsing(false) }}
                />
              ))}
            </>
          )}
          {relevant.length === 0 && (
            <p className="text-xs text-base-500 text-center py-3">No models found for this yet.</p>
          )}
        </div>
      )}
    </div>
  )
}
