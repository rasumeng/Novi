import { ShieldCheck } from 'lucide-react'
import { ToolsSettings } from './ToolsSettings'
import type { ToolInfo } from './types'
import { useFrameworkSettings } from '@/hooks/useFrameworkSettings'

interface Props {
  tools: ToolInfo[]
  framework: ReturnType<typeof useFrameworkSettings>
}

/**
 * Permissions — what Novi is allowed to do on its own.
 *
 * M4.1 checkpoint: this page owns the existing per-tool permission
 * configuration (Allow / Ask / Deny). Permissions are distinct from
 * Connectors: a connector is an external capability source, while a
 * permission decides whether and how Novi may act.
 */
export function PermissionsSettings({ tools, framework }: Props) {
  return (
    <div className="space-y-5">
      <div className="flex items-start gap-3">
        <div className="w-9 h-9 rounded-xl bg-accent/15 text-accent flex items-center justify-center shrink-0">
          <ShieldCheck size={17} />
        </div>
        <div>
          <p className="text-sm text-base-100 font-medium">What Novi is allowed to do</p>
          <p className="text-xs text-base-500 mt-0.5">
            Permission for each tool — not the tools themselves. Connectors set up the connections; this page decides how Novi may act through them.
          </p>
        </div>
      </div>
      <ToolsSettings tools={tools} framework={framework} />
    </div>
  )
}
