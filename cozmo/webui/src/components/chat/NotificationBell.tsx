import { useState, useRef, useEffect } from 'react'
import { Bell, BellDot, CheckCircle, XCircle, Info, TriangleAlert, ShieldAlert, X, Trash2, BellOff } from 'lucide-react'
import { useNotificationCenter } from '@/hooks/useNotificationCenter'
import { Severity, NotificationAction } from '@/notifications/types'
import { EmptyState } from '@/components/common/EmptyState'

interface Props {
  onSelectConversation?: (id: string) => void
}

type IconRecord = Record<Severity, { icon: React.ElementType; color: string }>

const SEVERITY_META: IconRecord = {
  info: { icon: Info, color: 'text-accent' },
  success: { icon: CheckCircle, color: 'text-emerald-400' },
  warning: { icon: TriangleAlert, color: 'text-amber-400' },
  error: { icon: XCircle, color: 'text-red-400' },
  attention: { icon: ShieldAlert, color: 'text-amber-300' },
}

function timeAgo(ts: number): string {
  const sec = Math.round((Date.now() - ts) / 1000)
  if (sec < 60) return 'just now'
  const min = Math.round(sec / 60)
  if (min < 60) return `${min}m ago`
  const hr = Math.round(min / 60)
  return `${hr}h ago`
}

function targetConversationId(action?: NotificationAction, conversationId?: string): string | null {
  if (action?.type === 'openConversation') return action.conversationId
  return conversationId ?? null
}

export function NotificationBell({ onSelectConversation }: Props) {
  const [open, setOpen] = useState(false)
  const ref = useRef<HTMLDivElement>(null)
  const { notifications, unreadCount, markAllRead, dismiss, clear } = useNotificationCenter()

  useEffect(() => {
    if (!open) return
    const close = (e: MouseEvent) => {
      if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false)
    }
    document.addEventListener('mousedown', close)
    return () => document.removeEventListener('mousedown', close)
  }, [open])

  useEffect(() => {
    if (!open) return
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') setOpen(false)
    }
    document.addEventListener('keydown', onKey)
    return () => document.removeEventListener('keydown', onKey)
  }, [open])

  const toggle = () => {
    setOpen((v) => {
      const next = !v
      if (next) markAllRead()
      return next
    })
  }

  return (
    <div ref={ref} className="relative">
      <button
        onClick={toggle}
        aria-label={notifications.length > 0 ? `Notifications (${unreadCount} unread)` : 'Notifications'}
        aria-expanded={open}
        className="relative p-1.5 rounded-lg text-base-400 hover:text-base-100 hover:bg-base-800 transition-colors"
      >
        {unreadCount > 0 ? <BellDot size={14} /> : <Bell size={14} />}
        {unreadCount > 0 && (
          <span className="absolute -top-0.5 -right-0.5 w-2 h-2 rounded-full bg-accent" aria-hidden="true" />
        )}
      </button>
      {open && (
        <div className="absolute right-0 top-full mt-1 w-80 bg-base-850 border border-base-700 rounded-xl shadow-panel overflow-hidden z-50">
          <div className="flex items-center justify-between px-3 py-2 border-b border-base-700">
            <span className="text-[11px] font-medium text-base-300">Notifications</span>
            {notifications.length > 0 && (
              <button
                onClick={clear}
                aria-label="Clear all notifications"
                className="flex items-center gap-1 text-[10px] text-base-500 hover:text-base-200 transition-colors"
              >
                <Trash2 size={11} /> Clear
              </button>
            )}
          </div>
          {notifications.length === 0 ? (
            <EmptyState
              compact
              icon={BellOff}
              title="Nothing here yet"
              description="Job and response activity will show up here."
            />
          ) : (
            <div className="max-h-72 overflow-y-auto">
              {notifications.map((n) => {
                const meta = SEVERITY_META[n.severity]
                const MetaIcon = meta.icon
                const convId = targetConversationId(n.action, n.conversationId)
                const clickable = !!convId && !!onSelectConversation
                return (
                  <div
                    key={n.id}
                    role={clickable ? 'button' : undefined}
                    tabIndex={clickable ? 0 : undefined}
                    aria-label={clickable ? `${n.title}. ${n.message}. Open conversation.` : undefined}
                    onClick={() => {
                      if (clickable && convId) {
                        onSelectConversation!(convId)
                        setOpen(false)
                      }
                    }}
                    onKeyDown={(e) => {
                      if (clickable && convId && (e.key === 'Enter' || e.key === ' ')) {
                        e.preventDefault()
                        onSelectConversation!(convId)
                        setOpen(false)
                      }
                    }}
                    className={`group flex items-start gap-2.5 px-3 py-2.5 border-b border-base-800/50 last:border-0 ${
                      clickable ? 'cursor-pointer hover:bg-base-800/50' : ''
                    } ${n.read ? 'opacity-80' : ''}`}
                  >
                    <MetaIcon size={13} className={`shrink-0 mt-0.5 ${meta.color}`} />
                    <div className="min-w-0 flex-1">
                      <div className="flex items-center justify-between gap-2">
                        <p className="text-[12px] font-medium text-base-100 leading-snug">{n.title}</p>
                        <span className="text-[10px] text-base-500 shrink-0">{timeAgo(n.createdAt)}</span>
                      </div>
                      <p className="text-[11px] text-base-400 leading-snug mt-0.5">{n.message}</p>
                    </div>
                    <button
                      onClick={(e) => { e.stopPropagation(); dismiss(n.id) }}
                      aria-label="Dismiss notification"
                      className="shrink-0 opacity-0 group-hover:opacity-100 focus:opacity-100 p-0.5 rounded text-base-500 hover:text-base-200 transition-all"
                    >
                      <X size={11} />
                    </button>
                  </div>
                )
              })}
            </div>
          )}
        </div>
      )}
    </div>
  )
}