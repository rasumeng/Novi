import { PanelLeft, PanelRight, Search, Settings } from 'lucide-react'
import { WindowControls } from './WindowControls'
import { NotificationBell } from '@/components/chat/NotificationBell'
import { GlobalActivityIndicator } from '@/components/chat/GlobalActivityIndicator'
import { ConnectionState } from '@/services/novi'
import { CONNECTION_LABEL } from '@/components/chat/connectionStatus'

interface Props {
  connection: ConnectionState
  reconnected?: boolean
  workingActivityTitle?: string | null
  isActiveConversation?: boolean
  onSelectConversation?: (id: string) => void
  collapsed: boolean
  onToggleSidebar: () => void
  activityOpen: boolean
  onToggleActivity: () => void
  onSearch: () => void
  onOpenSettings: () => void
}

export function TitleBar({
  connection,
  reconnected,
  workingActivityTitle,
  isActiveConversation,
  onSelectConversation,
  collapsed,
  onToggleSidebar,
  activityOpen,
  onToggleActivity,
  onSearch,
  onOpenSettings,
}: Props) {
  const conn = CONNECTION_LABEL[connection]

  return (
    <div className="flex h-8 w-full shrink-0 items-center bg-base-950 border-b border-base-800/40 px-2 gap-1">
      <button
        onClick={onToggleSidebar}
        aria-label={collapsed ? 'Expand sidebar' : 'Collapse sidebar'}
        className="shrink-0 p-1.5 rounded-md text-base-400 hover:text-base-100 hover:bg-base-800/60 transition-colors"
        title={collapsed ? 'Expand sidebar' : 'Collapse sidebar'}
      >
        <PanelLeft size={14} />
      </button>
      <button
          onClick={onSearch}
          aria-label="Search"
          className="p-1.5 rounded-md text-base-400 hover:text-base-100 hover:bg-base-800/60 transition-colors"
          title="Search"
        >
          <Search size={14} />
        </button>
      <div data-tauri-drag-region className="flex-1 h-full min-w-0" />

      <div className="flex shrink-0 items-center gap-0.5">
        <span className="hidden sm:flex items-center gap-1 px-1.5 text-[10px] text-base-500">
          <span className={`h-1.5 w-1.5 rounded-full ${conn.dot}`} />
        </span>
        {workingActivityTitle && (
          <GlobalActivityIndicator
            isActiveConversation={!!isActiveConversation}
            title={workingActivityTitle}
          />
        )}
        {reconnected && (
          <span className="rounded-full border border-ok/25 bg-ok/10 px-2 py-0.5 text-[10px] text-ok animate-fadeIn">
            Reconnected
          </span>
        )}
        
        <button
          onClick={onOpenSettings}
          aria-label="Settings"
          className="p-1.5 rounded-md text-base-400 hover:text-base-100 hover:bg-base-800/60 transition-colors"
          title="Settings"
        >
          <Settings size={14} />
        </button>
        <NotificationBell onSelectConversation={onSelectConversation} />
        <button
          onClick={onToggleActivity}
          aria-label={activityOpen ? 'Hide activity' : 'Show activity'}
          aria-expanded={activityOpen}
          className={`p-1.5 rounded-md transition-colors ${activityOpen ? 'text-base-100 bg-base-800/60' : 'text-base-400 hover:text-base-100 hover:bg-base-800/60'}`}
          title={activityOpen ? 'Hide activity' : 'Show activity'}
        >
          <PanelRight size={14} />
        </button>
      </div>

      <WindowControls />
    </div>
  )
}
