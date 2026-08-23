import { describe, it, expect, vi, beforeEach } from 'vitest'
import { renderHook, act, waitFor } from '@testing-library/react'
import type { Conversation } from '@/types'
import type { ServerEvent } from '@/services/cozmo'
import { ToastProvider } from './useToast'
import { NotificationCenterProvider, useNotificationCenter } from './useNotificationCenter'

// A test double for CozmoClient. Each instance is recorded so tests can grab
// the one the hook created and drive it directly with server events —
// simulating what would arrive over the WebSocket.
class MockCozmoClient {
  static instances: MockCozmoClient[] = []
  onEvent: (ev: ServerEvent) => void = () => {}
  onConnectionChange: (state: string) => void = () => {}
  sent: Array<{ content: string; conversationId?: string; deepResearch?: boolean }> = []

  constructor() {
    MockCozmoClient.instances.push(this)
  }
  connect() {
    this.onConnectionChange('open')
  }
  disconnect() {}
  sendChat(content: string, conversationId?: string, _attachments?: unknown, _projectId?: string, deepResearch?: boolean) {
    this.sent.push({ content, conversationId, deepResearch })
    return true
  }
  stop() { return true }
  answerPermission() { return true }
  answerPlan() { return true }
  reset() { return true }
  startBackgroundRun() { return true }
  stopBackgroundRun() { return true }
  listBackgroundRuns() { return true }

  emit(ev: ServerEvent) {
    this.onEvent(ev)
  }

  static latest(): MockCozmoClient {
    return MockCozmoClient.instances[MockCozmoClient.instances.length - 1]
  }
}

const convA: Conversation = { id: 'A', title: 'Conversation A', updatedAt: '', pinned: false, messages: [] }
const convB: Conversation = { id: 'B', title: 'Conversation B', updatedAt: '', pinned: false, messages: [] }

vi.mock('@/services/cozmo', () => ({
  CozmoClient: MockCozmoClient,
  fetchConversations: vi.fn(async () => [convA, convB]),
  saveConversation: vi.fn(async () => {}),
  deleteConversationApi: vi.fn(async () => {}),
  fetchProjects: vi.fn(async () => []),
  createProject: vi.fn(async () => null),
  updateProject: vi.fn(async () => null),
  deleteProjectApi: vi.fn(async () => {}),
  fetchProjectConversations: vi.fn(async () => []),
  fetchTimeline: vi.fn(async () => []),
}))

// Imported after the mock so the hook picks up MockCozmoClient.
const { useCozmoChat } = await import('./useCozmoChat')

function findConv(list: Conversation[], id: string) {
  return list.find((c) => c.id === id)
}

function Providers({ children }: { children: React.ReactNode }) {
  return (
    <ToastProvider>
      <NotificationCenterProvider>{children}</NotificationCenterProvider>
    </ToastProvider>
  )
}

function renderChatHook() {
  return renderHook(() => ({ chat: useCozmoChat(), notifications: useNotificationCenter() }), { wrapper: Providers })
}

beforeEach(() => {
  MockCozmoClient.instances = []
})

describe('generation ownership', () => {
  it('routes streaming tokens to the conversation that started the generation, not the one on screen', async () => {
    const { result } = renderChatHook()

    await waitFor(() => expect(result.current.chat.conversations).toHaveLength(2))

    // 1. Start generation in conversation A.
    act(() => result.current.chat.setActiveId('A'))
    act(() => result.current.chat.sendMessage('hello from A'))
    expect(result.current.chat.active.id).toBe('A')
    expect(result.current.chat.generating).toBe(true)

    const client = MockCozmoClient.latest()
    expect(client.sent[0]).toMatchObject({ content: 'hello from A', conversationId: 'A' })

    // 2. Switch to conversation B while A is still generating.
    act(() => result.current.chat.setActiveId('B'))
    expect(result.current.chat.active.id).toBe('B')
    // Viewed conversation isn't the owner, so the UI must not claim it's generating.
    expect(result.current.chat.generating).toBe(false)
    // sendMessage retitles a first message onto the conversation, so the busy
    // banner reflects that title rather than the original placeholder.
    expect(result.current.chat.busyReason).toContain('hello from A')
    // The raw, ungated owner id is exposed regardless of which conversation is on screen.
    expect(result.current.chat.generatingConversationId).toBe('A')

    // 3. Receive streaming tokens while B is on screen.
    act(() => client.emit({ type: 'token', text: 'Hi ' }))
    act(() => client.emit({ type: 'token', text: 'there' }))

    // 4. Tokens must appear only in A.
    const convAAfter = findConv(result.current.chat.conversations, 'A')
    expect(convAAfter?.messages.some((m) => m.role === 'assistant' && m.content === 'Hi there')).toBe(true)

    // 5. B must remain unchanged.
    const convBAfter = findConv(result.current.chat.conversations, 'B')
    expect(convBAfter?.messages).toEqual([])

    // The trace panel for the on-screen conversation (B) must stay empty even
    // though a "thinking" step was pushed for the in-flight generation.
    act(() => client.emit({ type: 'thinking', text: 'Reasoning...' }))
    expect(result.current.chat.inlineSteps).toEqual([])

    // Switching back to A reveals the same generation state again — nothing
    // was lost or misrouted, it was just hidden while B was on screen.
    act(() => result.current.chat.setActiveId('A'))
    expect(result.current.chat.generating).toBe(true)
    expect(result.current.chat.inlineSteps.length).toBeGreaterThan(0)

    act(() => client.emit({ type: 'done' }))
    expect(result.current.chat.generating).toBe(false)
    expect(result.current.chat.busyReason).toBeNull()
    expect(result.current.chat.generatingConversationId).toBeNull()
  })

  it('refuses to start a second generation while one is already in flight (single-flight)', async () => {
    const { result } = renderChatHook()
    await waitFor(() => expect(result.current.chat.conversations).toHaveLength(2))

    act(() => result.current.chat.setActiveId('A'))
    act(() => result.current.chat.sendMessage('first'))
    const client = MockCozmoClient.latest()
    expect(client.sent).toHaveLength(1)

    act(() => result.current.chat.setActiveId('B'))
    act(() => result.current.chat.sendMessage('second, from B, while A is busy'))
    // No second chat frame should have been sent — the backend is single-flight.
    expect(client.sent).toHaveLength(1)
    expect(findConv(result.current.chat.conversations, 'B')?.messages).toEqual([])
  })

  it('does not leave the UI stuck generating forever if stop() never gets a done event', async () => {
    const { result } = renderChatHook()
    // Let the initial data fetch resolve on real timers before switching to fake ones.
    await waitFor(() => expect(result.current.chat.conversations).toHaveLength(2))

    vi.useFakeTimers()
    try {
      act(() => result.current.chat.setActiveId('A'))
      act(() => result.current.chat.sendMessage('hello'))
      expect(result.current.chat.generating).toBe(true)

      act(() => result.current.chat.stop())
      act(() => vi.advanceTimersByTime(9000))

      expect(result.current.chat.generating).toBe(false)
    } finally {
      vi.useRealTimers()
    }
  })
})

describe('cross-conversation notifications', () => {  it('pushes a notification when a response finishes off-screen, not when it finishes on-screen', async () => {
    const { result } = renderChatHook()
    await waitFor(() => expect(result.current.chat.conversations).toHaveLength(2))

    act(() => result.current.chat.setActiveId('A'))
    act(() => result.current.chat.sendMessage('hello from A'))
    const client = MockCozmoClient.latest()

    // Switch away before it finishes.
    act(() => result.current.chat.setActiveId('B'))
    act(() => client.emit({ type: 'done' }))

    expect(result.current.notifications.notifications).toHaveLength(1)
    expect(result.current.notifications.notifications[0]).toMatchObject({ conversationId: 'A', severity: 'success' })

    // Now do the same but stay on the conversation that's generating — no notification expected.
    act(() => result.current.chat.setActiveId('B'))
    act(() => result.current.chat.sendMessage('hello from B'))
    act(() => client.emit({ type: 'done' }))
    expect(result.current.notifications.notifications).toHaveLength(1) // unchanged
  })
})

describe('deep research mode', () => {
  it('threads an explicit deep_research flag into sendChat when enabled', async () => {
    const { result } = renderChatHook()
    await waitFor(() => expect(result.current.chat.conversations).toHaveLength(2))

    act(() => result.current.chat.setActiveId('A'))
    expect(result.current.chat.deepResearch).toBe(false)

    act(() => result.current.chat.toggleDeepResearch())
    expect(result.current.chat.deepResearch).toBe(true)

    act(() => result.current.chat.sendMessage('deep dive', undefined, true))
    const client = MockCozmoClient.latest()
    expect(client.sent[0]).toMatchObject({ content: 'deep dive', conversationId: 'A', deepResearch: true })

    // Mode is per-conversation: B stays off, A stays on.
    act(() => result.current.chat.setActiveId('B'))
    expect(result.current.chat.deepResearch).toBe(false)
    act(() => result.current.chat.setActiveId('A'))
    expect(result.current.chat.deepResearch).toBe(true)
  })

  it('keeps deep research off when the flag is not requested', async () => {
    const { result } = renderChatHook()
    await waitFor(() => expect(result.current.chat.conversations).toHaveLength(2))
    act(() => result.current.chat.setActiveId('A'))
    act(() => result.current.chat.sendMessage('plain message'))
    expect(MockCozmoClient.latest().sent[0].deepResearch).toBeUndefined()
  })
})

describe('agent phase activity (Phase 8G)', () => {
  it('maps research phase events to user-facing labels without graph topology', async () => {
    const { result } = renderChatHook()
    await waitFor(() => expect(result.current.chat.conversations).toHaveLength(2))
    act(() => result.current.chat.setActiveId('A'))
    act(() => result.current.chat.sendMessage('research something'))
    const client = MockCozmoClient.latest()

    act(() => client.emit({ type: 'phase', phase: 'searching' }))
    act(() => client.emit({ type: 'phase', phase: 'evaluating' }))
    act(() => client.emit({ type: 'phase', phase: 'refining', gaps: 2 }))
    act(() => client.emit({ type: 'phase', phase: 'synthesizing' }))
    act(() => client.emit({ type: 'phase', phase: 'validating',
                            citations_used: true, insufficient: false }))

    const labels = result.current.chat.inlineSteps.map(s => s.label)
    expect(labels).toContain('Searching for information')
    expect(labels).toContain('Evaluating evidence quality')
    expect(labels).toContain('Refining the search')
    expect(labels).toContain('Synthesizing findings')
    expect(labels).toContain('Validating citations')

    // No internal node names leak to the UI.
    const joined = JSON.stringify(result.current.chat.inlineSteps)
    expect(joined).not.toMatch(/node|graph|langgraph/i)
  })

  it('marks verification failure as an error step and retries as new steps', async () => {
    const { result } = renderChatHook()
    await waitFor(() => expect(result.current.chat.conversations).toHaveLength(2))
    act(() => result.current.chat.setActiveId('A'))
    act(() => result.current.chat.sendMessage('fix the bug'))
    const client = MockCozmoClient.latest()

    act(() => client.emit({ type: 'phase', phase: 'verifying' }))
    act(() => client.emit({ type: 'phase', phase: 'verification_failed',
                            command: 'pytest -q', exit_code: 2 }))
    act(() => client.emit({ type: 'retry', phase: 'retry', attempt: 2,
                            reason: 'verification_failed' }))

    const steps = result.current.chat.inlineSteps
    expect(steps.some(s => s.label === 'Verifying the changes')).toBe(true)
    const failed = steps.find(s => s.status === 'error')
    expect(failed?.label).toBe('Verification failed — analyzing what went wrong')
    expect(failed?.detail).toContain('pytest -q')
    expect(steps.some(s => s.label.includes('attempt 2'))).toBe(true)
  })
})

describe('reasoning thought block', () => {
  it('accumulates reasoning events and attaches the trace to the assistant message on first token', async () => {
    const { result } = renderChatHook()
    await waitFor(() => expect(result.current.chat.conversations).toHaveLength(2))

    act(() => result.current.chat.setActiveId('A'))
    act(() => result.current.chat.sendMessage('hard problem'))
    const client = MockCozmoClient.latest()

    act(() => client.emit({ type: 'reasoning', text: 'step one ' }))
    act(() => client.emit({ type: 'reasoning', text: 'step two' }))
    act(() => client.emit({ type: 'token', text: 'Answer' }))

    const assistant = findConv(result.current.chat.conversations, 'A')?.messages.find((m) => m.role === 'assistant')
    expect(assistant?.content).toBe('Answer')
    expect(assistant?.thought).toBe('step one step two')
    expect(assistant?.thoughtElapsedMs).toBeGreaterThanOrEqual(0)

    // The trace is drained — subsequent tokens don't re-append it.
    act(() => client.emit({ type: 'token', text: ' extended' }))
    const after = findConv(result.current.chat.conversations, 'A')?.messages.find((m) => m.role === 'assistant')
    expect(after?.content).toBe('Answer extended')
    expect(after?.thought).toBe('step one step two')
  })

  it('exposes a live thinking trace while reasoning is in flight', async () => {
    const { result } = renderChatHook()
    await waitFor(() => expect(result.current.chat.conversations).toHaveLength(2))

    act(() => result.current.chat.setActiveId('A'))
    act(() => result.current.chat.sendMessage('hard problem'))
    const client = MockCozmoClient.latest()

    expect(result.current.chat.thinking).toBe(false)
    expect(result.current.chat.liveThought).toBe('')

    act(() => client.emit({ type: 'reasoning', text: 'step one ' }))
    expect(result.current.chat.thinking).toBe(true)
    expect(result.current.chat.liveThought).toBe('step one ')

    act(() => client.emit({ type: 'reasoning', text: 'step two' }))
    expect(result.current.chat.liveThought).toBe('step one step two')

    // First token ends the thinking phase: the trace collapses into the
    // message's thought block and the live state drains.
    act(() => client.emit({ type: 'token', text: 'Answer' }))
    expect(result.current.chat.thinking).toBe(false)
    expect(result.current.chat.liveThought).toBe('')
  })

  it('assistant messages without reasoning carry no thought block', async () => {
    const { result } = renderChatHook()
    await waitFor(() => expect(result.current.chat.conversations).toHaveLength(2))

    act(() => result.current.chat.setActiveId('A'))
    act(() => result.current.chat.sendMessage('simple'))
    act(() => MockCozmoClient.latest().emit({ type: 'token', text: 'Hi' }))

    const assistant = findConv(result.current.chat.conversations, 'A')?.messages.find((m) => m.role === 'assistant')
    expect(assistant?.thought).toBeUndefined()
  })
})
