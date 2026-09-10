import { useCallback, useEffect, useState } from 'react'
import { CheckCircle2, Cpu, Download, Monitor, Settings, ShieldCheck, Cable, AlertTriangle, RefreshCw, CircleAlert } from 'lucide-react'
import { fetchSystemHealth, type DiscoveryPayload, type SchemaResponse, type SystemHealth } from './api'
import type { SectionId } from './types'
import { LoadingSkeleton } from '@/components/common/LoadingSkeleton'
import { primaryModelFromDiscovery } from './api'
import { CapabilityChips } from '@/components/common/CapabilityChips'

interface Props {
  discovery: DiscoveryPayload | null
  schema: SchemaResponse | null
  installing: Record<string, { phase: string; pct: number | null }>
  onInstall: (name: string) => Promise<boolean>
  onNavigate: (section: SectionId) => void
  onRefresh: () => Promise<void>
  loading: boolean
}

/**
 * General — a concise overview/status surface for Novi. This is an
 * information page (what's running, what's configured, what needs attention),
 * not a configuration dump. It offers quick links into the appropriate
 * settings pages rather than hosting the controls itself.
 */

// Mirrors the dev hatch in SettingsModal.tsx (kept local to avoid an import
// cycle: SettingsModal imports GeneralSettings). The "More settings"
// QuickLink stays hidden until unlocked via `developer` search or
// localStorage `novi_dev=1`.
function isDevUnlocked(): boolean {
  try {
    return localStorage.getItem('novi_dev') === '1'
  } catch {
    return false
  }
}

export function GeneralSettings({ discovery, schema, installing, onInstall, onNavigate, onRefresh, loading }: Props) {
  const [health, setHealth] = useState<SystemHealth | null>(null)
  const [checking, setChecking] = useState(true)
  const refreshHealth = useCallback(async () => {
    setChecking(true)
    setHealth(await fetchSystemHealth())
    setChecking(false)
  }, [])
  useEffect(() => { void refreshHealth() }, [refreshHealth])
  if (loading || !discovery) return <LoadingSkeleton rows={4} compact />

  const hardware = discovery.hardware
  const missing = discovery.missingModels
  const primary = primaryModelFromDiscovery(discovery)
  const isUnreachable = discovery.ollamaReachable === false || discovery.status === 'error' || discovery.status === 'degraded'

  return (
    <div className="space-y-5">
      <SystemHealthCard health={health} checking={checking} onRefresh={refreshHealth} onNavigate={onNavigate} />
      {/* Novi status */}
      <div className="flex items-center gap-3 p-4 rounded-2xl border border-base-700 bg-base-800/40">
        <div className="w-10 h-10 rounded-xl bg-emerald-500/15 text-emerald-400 flex items-center justify-center shrink-0">
          <CheckCircle2 size={18} />
        </div>
        <div className="flex-1 min-w-0">
          <p className="text-sm font-semibold text-base-100">Novi is running</p>
          <p className="text-xs text-base-500 mt-0.5">
            Configuration and model resolution are live and saved automatically.
          </p>
        </div>
        <span className="text-[10px] font-medium text-emerald-400 bg-emerald-500/10 px-2 py-1 rounded-full border border-emerald-500/20 shrink-0">
          Healthy
        </span>
      </div>

      {/* Model mode / assignment */}
      <div className="p-4 rounded-2xl border border-base-700 bg-base-800/40">
        <div className="flex items-center gap-2 mb-3">
          <Cpu size={14} className="text-accent" />
          <p className="text-sm font-medium text-base-100">Model setup</p>
          <div className="flex-1" />
          <button
            onClick={() => onNavigate('models')}
            className="text-[11px] text-accent hover:text-accent/80 transition-colors"
          >
            Manage models →
          </button>
        </div>

        {primary ? (
          <div className="space-y-1.5">
            <p className="text-[11px] text-base-500 font-medium">Novi Model</p>
            <div className="flex items-center justify-between px-3 py-1.5 rounded-lg bg-base-900/40 border border-base-700/40">
              <span className="flex items-center gap-2 text-xs text-base-400">
                Novi Model
                {discovery.capabilities && <CapabilityChips caps={discovery.capabilities} />}
              </span>
              <span className="text-xs text-base-200 font-mono">{primary}</span>
            </div>
            <p className="text-[11px] text-base-500">This model powers Novi's conversations, coding, research, and agent tasks.</p>
          </div>
        ) : (
          <p className="text-xs text-base-500">
            No model selected yet — choose Novi's brain from the Models page.
          </p>
        )}
      </div>

      <div className="p-4 rounded-2xl border border-base-700 bg-base-800/40">
        <div className="flex items-center justify-between gap-3 mb-3">
          <div className="flex items-center gap-2">
            <Monitor size={14} className="text-base-500" />
            <p className="text-sm font-medium text-base-100">Hardware</p>
          </div>
          <button onClick={() => void onRefresh()} className="text-[11px] text-base-400 hover:text-base-200 transition-colors">Refresh</button>
        </div>
        <div className="grid grid-cols-1 sm:grid-cols-3 gap-2 text-xs">
          <HardwareFact label="GPU" value={hardware.gpu?.name || 'Not detected'} />
          <HardwareFact label="Video memory" value={hardware.gpu?.vramTotalGb == null ? 'Not detected' : `${hardware.gpu.vramTotalGb} GB`} />
          <HardwareFact label="System memory" value={hardware.ramGb > 0 ? `${hardware.ramGb} GB` : 'Not detected'} />
        </div>
        <p className="mt-2 text-[11px] text-base-500">Detection confidence: {hardware.confidence || 'unknown'}</p>
      </div>

      {/* Warnings — honest Ollama status */}
      {isUnreachable && (
        <div data-testid="general-ollama-unreachable" className="flex items-center justify-between gap-3 p-3 rounded-xl border border-amber-500/30 bg-amber-500/10">
          <p className="flex items-center gap-2 text-xs text-amber-300">
            <AlertTriangle size={14} className="shrink-0" />
            <span>
              Ollama not reachable at {discovery.ollamaUrl ?? 'http://localhost:11434'}
              {discovery.modelsStale ? ' — showing cached inventory.' : '.'}
            </span>
          </p>
        </div>
      )}
      {missing.length > 0 && (
        <Warnings missing={missing} installing={installing} onInstall={onInstall} onNavigate={onNavigate} />
      )}

      {/* Quick links */}
      <div className="pt-1">
        <p className="text-[11px] uppercase tracking-wide text-base-500 font-semibold mb-2">Configuration</p>
        <div className="grid grid-cols-3 gap-2">
          <QuickLink icon={Cable} label="Connectors" onClick={() => onNavigate('connectors')} />
          <QuickLink icon={ShieldCheck} label="Permissions" onClick={() => onNavigate('permissions')} />
          {isDevUnlocked() && (
            <QuickLink icon={Settings} label="More settings" onClick={() => onNavigate('developer')} />
          )}
        </div>
      </div>
    </div>
  )
}

function HardwareFact({ label, value }: { label: string; value: string }) {
  return <div className="rounded-lg border border-base-700/70 bg-base-900/40 px-2.5 py-2">
    <p className="text-[10px] uppercase tracking-wide text-base-500">{label}</p>
    <p className="mt-0.5 truncate text-xs text-base-200" title={value}>{value}</p>
  </div>
}

function SystemHealthCard({ health, checking, onRefresh, onNavigate }: {
  health: SystemHealth | null
  checking: boolean
  onRefresh: () => Promise<void>
  onNavigate: (section: SectionId) => void
}) {
  const healthy = health?.ready === true
  const unavailable = health === null && !checking
  const items = health ? [
    ['Ollama', health.ollama.ready],
    ['Novi model', health.primaryModel.ready],
    ['Memory embeddings', health.embedding.ready],
  ] : []
  return <section className={`p-4 rounded-2xl border ${healthy ? 'border-emerald-500/25 bg-emerald-500/5' : 'border-amber-500/30 bg-amber-500/5'}`}>
    <div className="flex items-start gap-3">
      <div className={`w-10 h-10 rounded-xl flex items-center justify-center shrink-0 ${healthy ? 'bg-emerald-500/15 text-emerald-400' : 'bg-amber-500/15 text-amber-400'}`}>
        {healthy ? <CheckCircle2 size={18} /> : <CircleAlert size={18} />}
      </div>
      <div className="min-w-0 flex-1">
        <p className="text-sm font-semibold text-base-100">Local AI readiness</p>
        <p className="text-xs text-base-500 mt-0.5">
          {checking ? 'Checking your local models and memory…' : healthy ? 'Novi is ready for conversations and memory.' : unavailable ? 'Could not read the local readiness check.' : 'Finish these local requirements before relying on memory.'}
        </p>
      </div>
      <button onClick={() => void onRefresh()} disabled={checking} className="p-2 rounded-lg border border-base-700 text-base-400 hover:text-base-100 disabled:opacity-50" title="Recheck local AI readiness">
        <RefreshCw size={14} className={checking ? 'animate-spin' : ''} />
      </button>
    </div>
    {!checking && health && <>
      <div className="grid grid-cols-1 sm:grid-cols-3 gap-2 mt-3">
        {items.map(([label, ready]) => <div key={label as string} className="flex items-center gap-2 rounded-lg border border-base-700/70 bg-base-900/40 px-2.5 py-2 text-[11px]">
          <span className={`w-1.5 h-1.5 rounded-full ${ready ? 'bg-emerald-400' : 'bg-amber-400'}`} />
          <span className="text-base-300">{label as string}</span>
        </div>)}
      </div>
      {!health.ready && <div className="mt-3 flex items-center justify-between gap-3 text-[11px] text-base-400">
        <span>{health.embedding.error ?? health.ollama.error ?? 'Choose and install a local model to continue.'}</span>
        <button onClick={() => onNavigate('models')} className="shrink-0 text-accent hover:text-accent/80">Open Models →</button>
      </div>}
    </>}
  </section>
}

function QuickLink({ icon: Icon, label, onClick }: {
  icon: React.ElementType
  label: string
  onClick: () => void
}) {
  return (
    <button
      onClick={onClick}
      className="flex flex-col items-center gap-1.5 p-3 rounded-xl border border-base-700 bg-base-800/40 hover:border-accent/40 hover:bg-base-800 text-base-300 hover:text-base-100 transition-colors"
    >
      <Icon size={15} className="text-base-400" />
      <span className="text-[11px] font-medium">{label}</span>
    </button>
  )
}

function Warnings({ missing, installing, onInstall, onNavigate }: {
  missing: string[]
  installing: Record<string, { phase: string; pct: number | null }>
  onInstall: (name: string) => Promise<boolean>
  onNavigate: (section: SectionId) => void
}) {
  return (
    <div className="p-4 rounded-2xl border border-amber-500/30 bg-amber-500/5">
      <div className="flex items-center gap-2 mb-2">
        <AlertTriangle size={14} className="text-amber-400" />
        <p className="text-sm font-medium text-amber-300">Models need attention</p>
      </div>
      <p className="text-xs text-base-500 mb-3">
        {missing.length} referenced model{missing.length > 1 ? 's' : ''} {missing.length > 1 ? 'are' : 'is'} not installed.
      </p>
      <div className="flex flex-wrap gap-1.5 mb-3">
        {missing.map((name) => {
          const state = installing[name]
          return (
            <div key={name} className="flex items-center gap-2 px-3 py-1.5 rounded-lg bg-base-900/40 border border-base-700/60">
              <span className="text-xs text-base-200 font-mono">{name}</span>
              {state && (
                <span className="text-[10px] text-base-500">{state.phase === 'done' ? 'Installed' : `${state.phase}…`}</span>
              )}
            </div>
          )
        })}
      </div>
      <div className="flex items-center gap-2">
        <button
          onClick={() => onNavigate('models')}
          className="flex items-center gap-1.5 px-3 py-1.5 text-xs font-medium rounded-lg bg-accent text-white hover:bg-accent/90 transition-colors"
        >
          <Download size={13} /> Go to Models
        </button>
        <span className="text-[11px] text-base-500">Install missing models from the Models page.</span>
      </div>
    </div>
  )
}
