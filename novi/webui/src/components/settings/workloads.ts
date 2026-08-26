import type { DiscoveryPayload, SchemaResponse } from './api'

export interface WorkloadMeta {
  key: string
  label: string
  desc: string
}

/**
 * Workload metadata derived from the backend configuration schema — the single
 * authoritative source for workload keys, labels, and descriptions. The keys
 * themselves come from the discovery payload so the surface stays backend
 * driven even when the schema is unavailable.
 */
export function workloadsFromDiscovery(discovery: DiscoveryPayload | null, schema: SchemaResponse | null): WorkloadMeta[] {
  const byKey = new Map<string, { label: string; desc: string }>()
  for (const s of schema?.settings ?? []) {
    const m = /^llm\.workloads\.([^.]+)\.model$/.exec(s.id)
    if (m) {
      byKey.set(m[1], { label: s.label, desc: s.description })
    }
  }
  return Object.keys(discovery?.workloads ?? {}).map((key) => ({
    key,
    label: byKey.get(key)?.label ?? key,
    desc: byKey.get(key)?.desc ?? '',
  }))
}