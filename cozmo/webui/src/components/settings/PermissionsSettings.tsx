import { ShieldCheck } from 'lucide-react'
import { ToolsSettings } from './ToolsSettings'
import type { ToolInfo, SettingsData } from './types'

interface Props {
  tools: ToolInfo[]
  config: SettingsData | null
  updateToolPermission: (toolId: string, mode: string) => void
}

/**
 * Permissions — what Cozmo is allowed to do on its own.
 *
 * M4.1 checkpoint: this page owns the existing per-tool permission
 * configuration (Allow / Ask / Deny). Permissions are distinct from
 * Connectors: a connector is an external capability source, while a
 * permission decides whether and how Cozmo may act.
 */
export function PermissionsSettings({ tools, config, updateToolPermission }: Props) {
  return (
    <div className="space-y-5">
      <div className="flex items-start gap-3">
        <div className="w-9 h-9 rounded-xl bg-accent/15 text-accent flex items-center justify-center shrink-0">
          <ShieldCheck size={17} />
        </div>
        <div>
          <p className="text-sm text-base-100 font-medium">What Cozmo is allowed to do</p>
          <p className="text-xs text-base-500 mt-0.5">
            Permission for each tool — not the tools themselves. Connectors set up the connections; this page decides how Cozmo may act through them.
          </p>
        </div>
      </div>
      <ToolsSettings tools={tools} config={config} updateToolPermission={updateToolPermission} />
    </div>
  )
}
