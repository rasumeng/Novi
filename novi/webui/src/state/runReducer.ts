export type RunStatus = 'idle' | 'queued' | 'running' | 'awaiting_permission' |
  'completed' | 'blocked' | 'failed' | 'cancelled' | 'interrupted'

export type MessageStatus = 'streaming' | 'completed' | 'failed' | 'cancelled'
export type ToolStatus = 'requested' | 'running' | 'succeeded' | 'failed' |
  'denied' | 'cancelled' | 'timed_out'

interface WireBase {
  type: string
  runId: string
  conversationId: string
  sequence: number
}

export type RunWireEvent = WireBase & {
  type: 'run_state' | 'message_start' | 'token' | 'message_end' |
    'tool_call' | 'tool.started' | 'tool_result' | 'permission_request' |
    'permission_resolved' | 'status' | 'done' | 'error' | 'cancelled'
  status?: string
  messageId?: string
  text?: string
  id?: string
  tool?: string
  args?: Record<string, unknown>
  result?: string
  diff?: unknown
  effects?: string[]
  digest?: string
  expiresAt?: string
  proposedDiff?: unknown
  decision?: string
}

export interface ProjectedMessage {
  id: string
  content: string
  status: MessageStatus
}

export interface ProjectedTool {
  id: string
  name: string
  arguments: Record<string, unknown>
  result: string
  status: ToolStatus
  diff?: unknown
}

export interface ProjectedPermission {
  id: string
  tool: string
  arguments: Record<string, unknown>
  effects: string[]
  digest?: string
  expiresAt?: string
  proposedDiff?: unknown
  status: 'pending' | 'allowed' | 'denied' | 'cancelled' | 'expired'
}

export interface RunProjection {
  conversationId: string
  runId: string | null
  lastSequence: number
  recoveryAfter: number | null
  status: RunStatus
  messageOrder: string[]
  messages: Record<string, ProjectedMessage>
  toolOrder: string[]
  tools: Record<string, ProjectedTool>
  permission: ProjectedPermission | null
  statusText: string
  error: string | null
}

export function createRunProjection(conversationId: string): RunProjection {
  return {
    conversationId, runId: null, lastSequence: 0, recoveryAfter: null,
    status: 'idle', messageOrder: [], messages: {}, toolOrder: [], tools: {},
    permission: null, statusText: '', error: null,
  }
}

export function reduceRunEvent(state: RunProjection, event: RunWireEvent): RunProjection {
  if (event.conversationId !== state.conversationId) return state
  if (state.runId && event.runId !== state.runId &&
      event.type === 'run_state' && event.sequence === 1) {
    state = createRunProjection(state.conversationId)
  }
  if (state.runId && event.runId !== state.runId) return state
  if (event.sequence <= state.lastSequence) return state
  if (event.sequence !== state.lastSequence + 1) {
    return { ...state, recoveryAfter: state.lastSequence }
  }

  let next: RunProjection = {
    ...state,
    runId: state.runId ?? event.runId,
    lastSequence: event.sequence,
    recoveryAfter: null,
  }

  switch (event.type) {
    case 'run_state':
      return { ...next, status: asRunStatus(event.status), error: null }
    case 'message_start':
      return startMessage(next, String(event.messageId ?? ''))
    case 'token': {
      const id = String(event.messageId ?? '')
      next = startMessage(next, id)
      return { ...next, messages: { ...next.messages, [id]: {
        ...next.messages[id], content: next.messages[id].content + String(event.text ?? ''),
      } } }
    }
    case 'message_end':
      return settleMessage(next, String(event.messageId ?? ''), 'completed')
    case 'tool_call': {
      const id = String(event.id ?? '')
      if (!id) return next
      const exists = !!next.tools[id]
      return { ...next, toolOrder: exists ? next.toolOrder : [...next.toolOrder, id],
        tools: { ...next.tools, [id]: {
          id, name: String(event.tool ?? ''),
          arguments: objectValue(event.args), result: '', status: 'requested',
        } } }
    }
    case 'tool.started': {
      const id = String(event.id ?? '')
      const current = next.tools[id]
      if (!current) return next
      return { ...next, tools: { ...next.tools, [id]: { ...current, status: 'running' } } }
    }
    case 'tool_result': {
      const id = String(event.id ?? '')
      const current = next.tools[id]
      if (!current) return next
      return { ...next, tools: { ...next.tools, [id]: {
        ...current, result: String(event.result ?? ''),
        status: asToolStatus(event.status), diff: event.diff,
      } } }
    }
    case 'permission_request':
      return { ...next, status: 'awaiting_permission', permission: {
        id: String(event.id ?? ''), tool: String(event.tool ?? ''),
        arguments: objectValue(event.args), effects: stringArray(event.effects),
        digest: optionalString(event.digest), expiresAt: optionalString(event.expiresAt),
        proposedDiff: event.proposedDiff, status: 'pending',
      } }
    case 'permission_resolved':
      if (!next.permission || next.permission.id !== event.id) return next
      return { ...next, status: 'running', permission: { ...next.permission,
        status: permissionStatus(event.decision) } }
    case 'status':
      return { ...next, statusText: String(event.text ?? '') }
    case 'done':
      return terminal(next, 'completed', null)
    case 'cancelled':
      return terminal(next, 'cancelled', null)
    case 'error':
      return terminal(next, asTerminalStatus(event.status), String(event.text ?? 'Run failed'))
    default:
      return next
  }
}

function startMessage(state: RunProjection, id: string): RunProjection {
  if (!id || state.messages[id]) return state
  return { ...state, messageOrder: [...state.messageOrder, id],
    messages: { ...state.messages, [id]: { id, content: '', status: 'streaming' } } }
}

function settleMessage(state: RunProjection, id: string, status: MessageStatus): RunProjection {
  const current = state.messages[id]
  if (!current) return state
  return { ...state, messages: { ...state.messages, [id]: { ...current, status } } }
}

function terminal(state: RunProjection, status: RunStatus, error: string | null): RunProjection {
  const messageStatus: MessageStatus = status === 'cancelled' ? 'cancelled' :
    status === 'completed' ? 'completed' : 'failed'
  const toolStatus: ToolStatus = status === 'cancelled' ? 'cancelled' : 'failed'
  return {
    ...state, status, error,
    messages: Object.fromEntries(Object.entries(state.messages).map(([id, message]) =>
      [id, message.status === 'streaming' ? { ...message, status: messageStatus } : message])),
    tools: Object.fromEntries(Object.entries(state.tools).map(([id, tool]) =>
      [id, ['requested', 'running'].includes(tool.status) ? { ...tool, status: toolStatus } : tool])),
    permission: state.permission?.status === 'pending'
      ? { ...state.permission, status: status === 'cancelled' ? 'cancelled' : 'denied' }
      : state.permission,
  }
}

function asRunStatus(value: unknown): RunStatus {
  const allowed: RunStatus[] = ['idle', 'queued', 'running', 'awaiting_permission',
    'completed', 'blocked', 'failed', 'cancelled', 'interrupted']
  return allowed.includes(value as RunStatus) ? value as RunStatus : 'running'
}

function asTerminalStatus(value: unknown): RunStatus {
  return ['blocked', 'failed', 'interrupted'].includes(String(value))
    ? value as RunStatus : 'failed'
}

function asToolStatus(value: unknown): ToolStatus {
  const allowed: ToolStatus[] = ['requested', 'running', 'succeeded', 'failed',
    'denied', 'cancelled', 'timed_out']
  return allowed.includes(value as ToolStatus) ? value as ToolStatus : 'failed'
}

function permissionStatus(value: unknown): ProjectedPermission['status'] {
  if (value === 'allowed') return 'allowed'
  if (value === 'expired') return 'expired'
  if (value === 'cancelled') return 'cancelled'
  return 'denied'
}

function objectValue(value: unknown): Record<string, unknown> {
  return value && typeof value === 'object' && !Array.isArray(value)
    ? value as Record<string, unknown> : {}
}

function stringArray(value: unknown): string[] {
  return Array.isArray(value) ? value.map(String) : []
}

function optionalString(value: unknown): string | undefined {
  return typeof value === 'string' ? value : undefined
}
