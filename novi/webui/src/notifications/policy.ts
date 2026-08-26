// Notification policy layer. The only place that turns a runtime event into
// notification content. Callers describe the event (via a policy helper) and
// get back a normalized draft plus what, if anything, should cross to the OS —
// keeping the in-app notification center and native notifications synchronized
// without scattering decisions across useNoviChat and components.

import type { NotificationDraft } from './types'

export interface NativePair {
  title: string
  body: string
}

export interface PolicyResult {
  draft: NotificationDraft
  /** Native OS notification. The caller applies the "only when unfocused" gate. */
  native: NativePair | null
}

const NATIVE_TITLE = 'Novi'

function quote(title: string): string {
  return `"${title}"`
}

function truncate(s: string, n: number): string {
  return s.length > n ? `${s.slice(0, Math.max(1, n - 1))}…` : s
}

export const notifyPolicy = {
  /** A long-running/background job finished successfully. */
  jobCompleted(goal: string): PolicyResult {
    return {
      draft: { severity: 'success', title: 'Job completed', message: `${goal} finished` },
      native: { title: NATIVE_TITLE, body: `Job completed: ${truncate(goal, 60)}` },
    }
  },

  /** A background job failed. */
  jobFailed(goal: string): PolicyResult {
    return {
      draft: { severity: 'error', title: 'Job failed', message: `${goal} ran into a problem` },
      native: { title: NATIVE_TITLE, body: `Job failed: ${truncate(goal, 60)}` },
    }
  },

  /** A response finished in a conversation (usually not the visible one). */
  responseReady(conversationTitle: string, conversationId: string): PolicyResult {
    return {
      draft: {
        severity: 'success',
        title: 'Response ready',
        message: `Response completed in ${quote(conversationTitle)}`,
        conversationId,
        action: { type: 'openConversation', conversationId },
      },
      native: { title: NATIVE_TITLE, body: `Response ready in ${truncate(conversationTitle, 60)}` },
    }
  },

  /** A response errored in a conversation. */
  responseFailed(conversationTitle: string, conversationId: string): PolicyResult {
    return {
      draft: {
        severity: 'error',
        title: 'Something went wrong',
        message: `Novi hit an error in ${quote(conversationTitle)}`,
        conversationId,
        action: { type: 'openConversation', conversationId },
      },
      native: { title: NATIVE_TITLE, body: `Something went wrong in ${truncate(conversationTitle, 60)}` },
    }
  },

  /** Runtime connection recovered after a drop. */
  reconnected(): PolicyResult {
    return {
      draft: { severity: 'info', title: 'Reconnected', message: 'Novi is back online' },
      native: null,
    }
  },
}