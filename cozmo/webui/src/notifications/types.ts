// Shared notification shapes. Single source of truth for severity levels and
// metadata so the notification center, policy layer, and bell all agree.

export type Severity = 'info' | 'success' | 'warning' | 'error' | 'attention'

export interface NotificationAction {
  type: 'openConversation'
  conversationId: string
}

export interface AppNotification {
  id: string
  createdAt: number
  severity: Severity
  title: string
  message: string
  read: boolean
  conversationId?: string
  action?: NotificationAction
}

/** Everything a caller needs to create a notification, minus bookkeeping. */
export interface NotificationDraft {
  severity: Severity
  title: string
  message: string
  conversationId?: string
  action?: NotificationAction
}
