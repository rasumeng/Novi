import { Eye, Wrench, Brain, Mic } from 'lucide-react'

export type WorkloadCaps = {
  vision?: boolean
  tools?: boolean
  reasoning?: boolean
  thinking?: boolean
  audio?: boolean
  coding?: boolean
}

const CHIP_DEFS: { key: keyof WorkloadCaps; label: string; icon: React.ElementType }[] = [
  { key: 'vision', label: 'Vision', icon: Eye },
  { key: 'tools', label: 'Tools', icon: Wrench },
  { key: 'reasoning', label: 'Thinking', icon: Brain },
  { key: 'audio', label: 'Audio', icon: Mic },
]

/**
 * CapabilityChips — renders [Vision][Thinking][Tools][Audio] for a model's capabilities.
 * Hide unsupported (no muted missing chips). Reasoning canonical, Thinking UI label.
 * Single source of truth: derive from ModelCapabilities (vision/tools/reasoning/audio).
 */
export function CapabilityChips({ caps, size = 'xs' }: { caps?: WorkloadCaps | null; size?: 'xs' | 'sm' }) {
  if (!caps) return null
  const active = CHIP_DEFS.filter((d) => {
    if (d.key === 'reasoning') return !!(caps.reasoning || caps.thinking)
    return !!(caps as Record<string, boolean>)[d.key]
  })
  if (active.length === 0) return null
  const sizeCls = size === 'xs' ? 'text-[10px] px-1.5 py-0.5 gap-1' : 'text-[11px] px-2 py-0.5 gap-1.5'
  return (
    <span className="inline-flex flex-wrap items-center gap-1">
      {active.map(({ key, label, icon: Icon }) => (
        <span
          key={key}
          data-cap={key}
          className={`inline-flex items-center rounded border font-medium bg-base-800 text-base-300 border-base-700/50 ${sizeCls}`}
        >
          <Icon size={size === 'xs' ? 10 : 11} className="shrink-0" />
          {label}
        </span>
      ))}
    </span>
  )
}

/** Derive WorkloadCaps from DiscoveredModelEntry capabilities dict (handles reasoning alias). */
export function capsFromEntry(entry?: { capabilities?: Record<string, boolean> } | null): WorkloadCaps | null {
  if (!entry?.capabilities) return null
  const c = entry.capabilities
  return {
    vision: !!c.vision,
    tools: !!c.tools,
    reasoning: !!(c.reasoning || c.thinking),
    thinking: !!(c.reasoning || c.thinking),
    audio: !!c.audio,
    coding: !!c.coding,
  }
}

/** Derive caps from discovery workload_capabilities map with fallback. */
export function capsFromWorkloadMap(
  workloadCaps: Record<string, WorkloadCaps> | undefined,
  workloadKey: string,
  fallbackEntry?: { capabilities?: Record<string, boolean> } | null,
): WorkloadCaps | null {
  if (workloadCaps?.[workloadKey]) return workloadCaps[workloadKey]
  return capsFromEntry(fallbackEntry)
}
