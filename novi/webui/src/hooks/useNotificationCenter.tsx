import { createContext, useCallback, useContext, useMemo, useState, type ReactNode } from 'react'
import type { AppNotification, NotificationDraft } from '@/notifications/types'

interface NotificationCenterValue {
  notifications: AppNotification[]
  unreadCount: number
  push: (n: NotificationDraft) => void
  markAllRead: () => void
  dismiss: (id: string) => void
  clear: () => void
}

const NotificationCenterContext = createContext<NotificationCenterValue | null>(null)

let idCounter = 0
const MAX_NOTIFICATIONS = 30

// Session-scoped notification history: job completion, responses finishing in
// a conversation the user isn't viewing, and reconnection events. Not persisted
// across app restarts — that remains a documented future enhancement.
export function NotificationCenterProvider({ children }: { children: ReactNode }) {
  const [notifications, setNotifications] = useState<AppNotification[]>([])

  const push = useCallback((n: NotificationDraft) => {
    setNotifications((prev) => {
      const next: AppNotification = { ...n, id: `notif-${Date.now()}-${idCounter++}`, createdAt: Date.now(), read: false }
      return [next, ...prev].slice(0, MAX_NOTIFICATIONS)
    })
  }, [])

  const markAllRead = useCallback(() => {
    setNotifications((prev) => prev.map((n) => ({ ...n, read: true })))
  }, [])

  const dismiss = useCallback((id: string) => {
    setNotifications((prev) => prev.filter((n) => n.id !== id))
  }, [])

  const clear = useCallback(() => setNotifications([]), [])

  const unreadCount = notifications.filter((n) => !n.read).length

  const value = useMemo<NotificationCenterValue>(
    () => ({ notifications, unreadCount, push, markAllRead, dismiss, clear }),
    [notifications, unreadCount, push, markAllRead, dismiss, clear]
  )

  return <NotificationCenterContext.Provider value={value}>{children}</NotificationCenterContext.Provider>
}

export function useNotificationCenter() {
  const ctx = useContext(NotificationCenterContext)
  if (!ctx) throw new Error('useNotificationCenter must be used within a NotificationCenterProvider')
  return ctx
}

export type { AppNotification }