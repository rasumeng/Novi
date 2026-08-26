import { WindowControls } from './WindowControls'
import { NotificationBell } from '@/components/chat/NotificationBell'
import { GlobalActivityIndicator } from '@/components/chat/GlobalActivityIndicator'
import { ConnectionState } from '@/services/novi'
import { CONNECTION_LABEL } from '@/components/chat/connectionStatus'

interface Props {
  /** Context shown after the Novi wordmark — the active conversation title or section name. */
  contextTitle?: string | null
  connection: ConnectionState
  /** True for a short window after a closed→open reconnect. */
  reconnected?: boolean
  /** Title of whichever conversation owns the current generation, or null when idle. */
  workingActivityTitle?: string | null
  /** True when the conversation on screen is the one generating. */
  isActiveConversation?: boolean
  /** Open a conversation (e.g. from a notification). */
  onSelectConversation?: (id: string) => void
}

export function TitleBar({
  contextTitle,
  connection,
  reconnected,
  workingActivityTitle,
  isActiveConversation,
  onSelectConversation,
}: Props) {
  const conn = CONNECTION_LABEL[connection]

  return (
    <div className="flex h-10 w-full shrink-0 items-center border-b border-base-800 bg-base-950">
      {/* Drag region / Novi branding + context */}
      <div
        data-tauri-drag-region
        className="flex h-full flex-1 min-w-0 select-none items-center gap-2.5 px-4"
      >
        <div className="flex shrink-0 items-center gap-2">
          <div className="flex h-5 w-5 items-center justify-center rounded-md bg-base-800 text-xs">
            ✦
          </div>
          <span className="text-sm font-medium text-base-100">
            Novi
          </span>
        </div>
        {contextTitle && (
          <div className="flex min-w-0 items-center gap-2 text-xs">
            <span className="text-base-700" aria-hidden="true">/</span>
            <span className="truncate text-base-400">{contextTitle}</span>
          </div>
        )}
      </div>

      {/* Live state: connection, activity, notifications */}
      <div className="flex h-full shrink-0 items-center gap-1 pr-2">
        <span className="flex items-center gap-1.5 px-1 text-[11px] text-base-400">
          <span className={`h-1.5 w-1.5 rounded-full ${conn.dot}`} />
          {conn.text}
        </span>
        {workingActivityTitle && (
          <GlobalActivityIndicator
            isActiveConversation={!!isActiveConversation}
            title={workingActivityTitle}
          />
        )}
        {reconnected && (
          <span className="rounded-full border border-ok/25 bg-ok/10 px-2 py-1 text-[11px] text-ok animate-fadeIn">
            Reconnected
          </span>
        )}
        <NotificationBell onSelectConversation={onSelectConversation} />
      </div>

      <WindowControls />
    </div>
  )
}