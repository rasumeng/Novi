import { useState, useRef, useEffect } from 'react'
import { Bell, BellDot, CheckCircle, XCircle, Info, BellOff, CheckCheck, Trash2 } from 'lucide-react'
import { useNotificationCenter } from '@/hooks/useNotificationCenter'

interface Props {
  onSelectConversation?: (id: string) => void
}

const ICONS = { success: CheckCircle, error: XCircle, info: Info }

function timeAgo(ts: number): string {
  const sec = Math.round((Date.now() - ts) / 1000)
  if (sec < 60) return 'just now'
  const min = Math.round(sec / 60)
  if (min < 60) return `${min}m ago`
  const hr = Math.round(min / 60)
  return `${hr}h ago`
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
        className="relative p-1.5 rounded-lg text-base-400 hover:text-base-100 hover:bg-base-800 transition-colors"
        title="Notifications"
      >
        {unreadCount > 0 ? <BellDot size={14} /> : <Bell size={14} />}
        {unreadCount > 0 && (
          <span className="absolute -top-0.5 -right-0.5 w-2 h-2 rounded-full bg-accent" />
        )}
      </button>
      {open && (
        <div className="absolute right-0 top-full mt-1 w-72 bg-base-850 border border-base-700 rounded-xl shadow-panel overflow-hidden z-50">
          <div className="flex items-center justify-between px-3 py-2 border-b border-base-700">
            <span className="text-[11px] font-medium text-base-300">Notifications</span>
            {notifications.length > 0 && (
              <button
                onClick={clear}
                className="flex items-center gap-1 text-[10px] text-base-500 hover:text-base-200 transition-colors"
                title="Clear all"
              >
                <Trash2 size={11} /> Clear
              </button>
            )}
          </div>
          {notifications.length === 0 ? (
            <div className="px-3 py-6 text-center">
              <BellOff size={20} className="mx-auto mb-2 text-base-600" />
              <p className="text-[12px] text-base-500">Nothing yet</p>
              <p className="text-[11px] text-base-600 mt-0.5">Job and response activity will show up here.</p>
            </div>
          ) : (
            <div className="max-h-64 overflow-y-auto">
              {notifications.map((n) => {
                const Icon = ICONS[n.kind]
                const clickable = !!n.conversationId && !!onSelectConversation
                return (
                  <div
                    key={n.id}
                    onClick={() => {
                      if (clickable) {
                        onSelectConversation!(n.conversationId!)
                        setOpen(false)
                      }
                    }}
                    className={`group flex items-start gap-2.5 px-3 py-2.5 border-b border-base-800/50 last:border-0 ${
                      clickable ? 'cursor-pointer hover:bg-base-800/50' : ''
                    }`}
                  >
                    <Icon size={13} className={`shrink-0 mt-0.5 ${
                      n.kind === 'success' ? 'text-emerald-400' :
                      n.kind === 'error' ? 'text-red-400' : 'text-accent'
                    }`} />
                    <div className="min-w-0 flex-1">
                      <p className="text-[12px] text-base-200 leading-snug">{n.text}</p>
                      <span className="text-[10px] text-base-500 mt-0.5 block">{timeAgo(n.createdAt)}</span>
                    </div>
                    <button
                      onClick={(e) => { e.stopPropagation(); dismiss(n.id) }}
                      className="shrink-0 opacity-0 group-hover:opacity-100 p-0.5 rounded text-base-500 hover:text-base-200 transition-all"
                      title="Dismiss"
                    >
                      <CheckCheck size={11} />
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
