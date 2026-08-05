import { CheckCircle2, AlertTriangle } from 'lucide-react'
import type { ModelCatalogEntry } from '@/product/types'
import { CAPABILITY_METADATA } from '@/product/capabilities'
import { TIER_INFO } from '@/product/modelTiers'
import { CapabilityBadge } from '@/components/common/CapabilityBadge'

const SPEED_LABEL: Record<ModelCatalogEntry['speed'], string> = {
  fast: 'Fast',
  balanced: 'Balanced speed',
  slow: 'Slower, more thorough',
}

const QUALITY_LABEL: Record<ModelCatalogEntry['quality'], string> = {
  good: 'Good quality',
  better: 'Better quality',
  best: 'Best quality',
}

interface Props {
  entry: ModelCatalogEntry
  selected?: boolean
  onSelect?: () => void
}

// One catalog entry, presented to build confidence rather than just label
// tier — supported models say *why* they're trusted, experimental models say
// *why* to be cautious, per the product direction. Reused for both "what's
// currently selected" and "browse to pick something else."
export function ModelCatalogCard({ entry, selected, onSelect }: Props) {
  const tier = TIER_INFO[entry.tier]
  const isSupported = entry.tier === 'supported'

  return (
    <button
      onClick={onSelect}
      disabled={!onSelect}
      className={`w-full text-left p-3 rounded-xl border transition-colors ${
        selected ? 'border-accent/50 bg-accent/[0.06]' : 'border-base-700 bg-base-800/40 hover:border-base-600'
      } ${!onSelect ? 'cursor-default' : ''}`}
    >
      <div className="flex items-start justify-between gap-2">
        <div className="min-w-0">
          <div className="flex items-center gap-1.5">
            <p className="text-sm font-medium text-base-100 truncate">{entry.displayName}</p>
            {selected && <CheckCircle2 size={13} className="text-accent shrink-0" />}
          </div>
          <p className="text-[11px] text-base-500 mt-0.5">
            {SPEED_LABEL[entry.speed]} · {QUALITY_LABEL[entry.quality]}
            {entry.approxRamGb ? ` · ~${entry.approxRamGb}GB RAM` : ''}
          </p>
        </div>
        <span
          className={`shrink-0 inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-[10px] font-medium ${
            isSupported
              ? 'text-emerald-400 bg-emerald-500/10 border border-emerald-500/20'
              : 'text-amber-400 bg-amber-500/10 border border-amber-500/20'
          }`}
        >
          {isSupported ? <CheckCircle2 size={11} /> : <AlertTriangle size={11} />}
          {tier.label}
        </span>
      </div>

      <div className="flex flex-wrap gap-1 mt-2">
        {entry.capabilities.map((c) => {
          const meta = CAPABILITY_METADATA[c]
          return <CapabilityBadge key={c} icon={meta.icon} label={meta.label} />
        })}
      </div>

      <p className="text-[10px] text-base-500 mt-2 leading-relaxed">
        {tier.reasons.join(' · ')}
      </p>
    </button>
  )
}
