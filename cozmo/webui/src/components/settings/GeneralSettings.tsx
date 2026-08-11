import { CheckCircle2, Cpu, Download, Monitor, Settings, ShieldCheck, Cable, AlertTriangle } from 'lucide-react'
import type { DiscoveryPayload } from './api'
import type { SectionId } from './types'
import { LoadingSkeleton } from '@/components/common/LoadingSkeleton'

interface Props {
  discovery: DiscoveryPayload | null
  mode: string
  source: string
  installing: Record<string, { phase: string; pct: number | null }>
  onInstall: (name: string) => Promise<boolean>
  onNavigate: (section: SectionId) => void
  loading: boolean
}

const ROLE_LABEL: Record<string, string> = {
  classifier: 'Classifier',
  router: 'Router',
  orchestrator: 'Orchestrator',
  chat: 'Chat',
  coder: 'Coder',
  planner: 'Planner',
  vision: 'Vision',
}

/**
 * General — a concise overview/status surface for Cozmo. This is an
 * information page (what's running, what's configured, what needs attention),
 * not a configuration dump. It offers quick links into the appropriate
 * settings pages rather than hosting the controls itself.
 */
export function GeneralSettings({ discovery, mode, source, installing, onInstall, onNavigate, loading }: Props) {
  if (loading || !discovery) return <LoadingSkeleton rows={4} compact />

  const hardware = discovery.hardware
  const missing = discovery.missingModels
  const assigned = Object.entries(discovery.roles ?? {}).filter(([, model]) => model)
  const modeLabel = mode === 'custom' ? 'Custom' : 'Automatic'

  return (
    <div className="space-y-5">
      {/* Cozmo status */}
      <div className="flex items-center gap-3 p-4 rounded-2xl border border-base-700 bg-base-800/40">
        <div className="w-10 h-10 rounded-xl bg-emerald-500/15 text-emerald-400 flex items-center justify-center shrink-0">
          <CheckCircle2 size={18} />
        </div>
        <div className="flex-1 min-w-0">
          <p className="text-sm font-semibold text-base-100">Cozmo is running</p>
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

        <div className="flex items-center justify-between p-3 rounded-xl bg-base-900/40 border border-base-700/60 mb-3">
          <div>
            <p className="text-sm text-base-100">{modeLabel} mode</p>
            <p className="text-xs text-base-500 mt-0.5">
              {source === 'custom'
                ? 'Models assigned manually.'
                : 'Cozmo resolves the best models automatically from what’s installed.'}
            </p>
          </div>
          <span className={`text-[10px] font-medium px-2 py-1 rounded-full border shrink-0 ${
            mode === 'custom'
              ? 'text-amber-400 bg-amber-500/10 border-amber-500/20'
              : 'text-sky-400 bg-sky-500/10 border-sky-500/20'
          }`}>
            {modeLabel}
          </span>
        </div>

        {assigned.length > 0 ? (
          <div className="space-y-1.5">
            <p className="text-[11px] text-base-500 font-medium">Active assignments</p>
            {assigned.slice(0, 4).map(([role, model]) => (
              <div key={role} className="flex items-center justify-between px-3 py-1.5 rounded-lg bg-base-900/40 border border-base-700/40">
                <span className="text-xs text-base-400">{ROLE_LABEL[role] ?? role}</span>
                <span className="text-xs text-base-200 font-mono">{model}</span>
              </div>
            ))}
          </div>
        ) : (
          <p className="text-xs text-base-500">No explicit role assignments — Cozmo is using its automatic resolution.</p>
        )}
      </div>

      {/* Hardware */}
      <div className="flex items-center justify-between p-3 rounded-xl bg-base-800/50 border border-base-700">
        <div className="flex items-center gap-2">
          <Monitor size={14} className="text-base-500" />
          <span className="text-sm text-base-200">Hardware detected</span>
        </div>
        <span className="text-xs text-base-400 font-mono">
          {hardware.ramGb > 0 ? `${hardware.ramGb} GB RAM` : 'unknown'}
        </span>
      </div>

      {/* Warnings */}
      {missing.length > 0 && (
        <Warnings missing={missing} installing={installing} onInstall={onInstall} onNavigate={onNavigate} />
      )}

      {/* Quick links */}
      <div className="pt-1">
        <p className="text-[11px] uppercase tracking-wide text-base-500 font-semibold mb-2">Configuration</p>
        <div className="grid grid-cols-3 gap-2">
          <QuickLink icon={Cable} label="Connectors" onClick={() => onNavigate('connectors')} />
          <QuickLink icon={ShieldCheck} label="Permissions" onClick={() => onNavigate('permissions')} />
          <QuickLink icon={Settings} label="More settings" onClick={() => onNavigate('developer')} />
        </div>
      </div>
    </div>
  )
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
