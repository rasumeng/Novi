import { useState } from 'react'
import { Zap, Sparkles, Gem, SlidersHorizontal, CheckCircle2, Monitor, Download } from 'lucide-react'
import type { DiscoveryPayload } from './api'
import { LoadingSkeleton } from '@/components/common/LoadingSkeleton'

const PRESET_ICON: Record<string, React.ElementType> = {
  light: Zap,
  medium: Sparkles,
  heavy: Gem,
  custom: SlidersHorizontal,
}

interface Props {
  discovery: DiscoveryPayload | null
  activeExperience: string
  installing: Record<string, { phase: string; pct: number | null }>
  onApply: (id: string) => Promise<boolean>
  onInstall: (name: string) => Promise<boolean>
  loading: boolean
}

/** General — how Cozmo behaves. Experience presets (experiences, not models). */
export function GeneralSettings({ discovery, activeExperience, installing, onApply, onInstall, loading }: Props) {
  const [applying, setApplying] = useState<string | null>(null)

  if (loading || !discovery) return <LoadingSkeleton rows={4} compact />

  const presets = discovery.presets
  const missing = discovery.missingModels
  const hardware = discovery.hardware

  const select = async (id: string) => {
    if (id === activeExperience) return
    setApplying(id)
    await onApply(id)
    setApplying(null)
  }

  return (
    <div className="space-y-5">
      <div>
        <p className="text-sm text-base-100 font-medium mb-1">Experience</p>
        <p className="text-xs text-base-500 mb-3">
          Choose how Cozmo behaves on this machine. Changes apply immediately — no save needed.
        </p>
        <div className="space-y-2.5">
          {presets.map((p) => {
            const Icon = PRESET_ICON[p.id] ?? Sparkles
            const isActive = p.id === activeExperience
            const busy = applying === p.id
            return (
              <button
                key={p.id}
                onClick={() => select(p.id)}
                disabled={busy}
                className={`w-full text-left p-4 rounded-2xl border transition-colors disabled:opacity-60 ${
                  isActive
                    ? 'border-accent/50 bg-accent/[0.06]'
                    : 'border-base-700 bg-base-800/40 hover:border-base-600'
                }`}
              >
                <div className="flex items-start gap-3">
                  <div className={`w-9 h-9 rounded-xl flex items-center justify-center shrink-0 ${isActive ? 'bg-accent/20 text-accent' : 'bg-base-800 text-base-400'}`}>
                    <Icon size={17} />
                  </div>
                  <div className="flex-1 min-w-0">
                    <div className="flex items-center gap-2">
                      <p className="text-sm font-semibold text-base-100">{p.label}</p>
                      {isActive && <CheckCircle2 size={14} className="text-accent shrink-0" />}
                      {busy && <span className="text-[11px] text-accent">Applying…</span>}
                    </div>
                    <p className="text-xs text-base-500 mt-0.5 leading-relaxed">{p.description}</p>
                  </div>
                </div>
              </button>
            )
          })}
        </div>
      </div>

      {/* Hardware readout — the recommendation engine reasons from this. */}
      <div className="flex items-center justify-between p-3 rounded-xl bg-base-800/50 border border-base-700">
        <div className="flex items-center gap-2">
          <Monitor size={14} className="text-base-500" />
          <span className="text-sm text-base-200">Your hardware</span>
        </div>
        <span className="text-xs text-base-400 font-mono">
          {hardware.ramGb > 0 ? `${hardware.ramGb} GB RAM` : 'unknown'}
        </span>
      </div>

      {missing.length > 0 && (
        <MissingModels missing={missing} installing={installing} onInstall={onInstall} />
      )}
    </div>
  )
}

function MissingModels({ missing, installing, onInstall }: {
  missing: string[]
  installing: Record<string, { phase: string; pct: number | null }>
  onInstall: (name: string) => Promise<boolean>
}) {
  return (
    <div className="space-y-2">
      <p className="text-sm text-base-100 font-medium">Missing models</p>
      <p className="text-xs text-base-500 mb-2">
        Your current configuration references models that aren't installed. Install them here — no terminal needed.
      </p>
      {missing.map((name) => {
        const state = installing[name]
        return (
          <div key={name} className="flex items-center justify-between p-3 rounded-xl bg-base-800/50 border border-base-700">
            <div>
              <p className="text-sm text-base-100 font-mono">{name}</p>
              {state && (
                <p className="text-xs text-base-500 mt-0.5">
                  {state.phase === 'done' ? 'Installed' : `${state.phase}${state.pct != null ? ` — ${state.pct}%` : ''}`}
                </p>
              )}
            </div>
            <button
              onClick={() => void onInstall(name)}
              disabled={!!state}
              className="flex items-center gap-1.5 px-3 py-1.5 text-xs font-medium rounded-lg bg-accent text-white hover:bg-accent/90 transition-colors disabled:opacity-60"
            >
              <Download size={13} />
              {state ? (state.phase === 'done' ? 'Installed' : 'Installing…') : 'Install'}
            </button>
          </div>
        )
      })}
    </div>
  )
}