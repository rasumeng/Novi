import { describe, it, expect, vi, beforeEach } from 'vitest'
import { renderHook, act, waitFor } from '@testing-library/react'
import type { Conversation } from '@/types'
import type { ServerEvent } from '@/services/novi'
import { ToastProvider } from './useToast'
import { NotificationCenterProvider, useNotificationCenter } from './useNotificationCenter'

// A test double for NoviClient. Each instance is recorded so tests can grab
// the one the hook created and drive it directly with server events —
// simulating what would arrive over the WebSocket.
class MockNoviClient {
  static instances: MockNoviClient[] = []
  onEvent: (ev: ServerEvent) => void = () => {}
  onConnectionChange: (state: string) => void = () => {}
  sent: Array<{ content: string; conversationId?: string; deepResearch?: boolean }> = []

  constructor() {
    MockNoviClient.instances.push(this)
  }
  connect() {
    this.onConnectionChange('open')
  }
  disconnect() {}
  sendChat(content: string, conversationId?: string, _attachments?: unknown, _projectId?: string, deepResearch?: boolean) {
    this.sequence = 0
    this.messageStarted = false
    this.sent.push({ content, conversationId, deepResearch })
    return true
  }
  stop() { return true }
  subscribeRun() { return true }
  answerPermission() { return true }
  answerPlan() { return true }
  reset() { return true }
  startBackgroundRun() { return true }
  stopBackgroundRun() { return true }
  listBackgroundRuns() { return true }

  emit(ev: ServerEvent) {
    this.onEvent(ev)
  }

  private sequence = 0
  private messageStarted = false

  // Generate the same sequenced envelopes used by the backend, not legacy tokens.
  emitRun(event: ServerEvent) {
    const send = (fields: object) => this.emit({
      ...fields, runId: `run-${this.sent.length}`, conversationId: this.sent[this.sent.length - 1].conversationId,
      sequence: ++this.sequence,
    } as ServerEvent)
    if (!this.sequence) send({ type: 'run_state', status: 'running' })
    if (['token', 'reasoning'].includes(event.type) && !this.messageStarted) {
      send({ type: 'message_start', messageId: 'reply' })
      this.messageStarted = true
    }
    send({ ...event, ...(['token', 'reasoning'].includes(event.type) ? { messageId: 'reply' } : {}) })
  }

  static latest(): MockNoviClient {
    return MockNoviClient.instances[MockNoviClient.instances.length - 1]
  }
}

const convA: Conversation = { id: 'A', title: 'Conversation A', updatedAt: '', pinned: false, messages: [] }
const convB: Conversation = { id: 'B', title: 'Conversation B', updatedAt: '', pinned: false, messages: [] }

vi.mock('@/services/novi', () => ({
  API_BASE: '',
  NoviClient: MockNoviClient,
  fetchConversations: vi.fn(async () => [convA, convB]),
  saveConversation: vi.fn(async () => {}),
  deleteConversationApi: vi.fn(async () => {}),
  fetchProjects: vi.fn(async () => []),
  createProject: vi.fn(async () => null),
  updateProject: vi.fn(async () => null),
  deleteProjectApi: vi.fn(async () => {}),
  fetchProjectConversations: vi.fn(async () => []),
  fetchTimeline: vi.fn(async () => []),
  fetchTimelineEnvelope: vi.fn(async () => ({ status: 'ok', brainAvailable: true, data: [] })),
}))

// Imported after the mock so the hook picks up MockNoviClient.
const { useNoviChat } = await import('./useNoviChat')
const { resetBootCache } = await import('./bootCache')
const { fetchProjects } = await import('@/services/novi')

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
  return renderHook(() => ({ chat: useNoviChat(), notifications: useNotificationCenter() }), { wrapper: Providers })
}

beforeEach(() => {
  vi.stubGlobal('fetch', vi.fn(async () => ({ ok: true, json: async () => ({ state: 'idle', version: 0,
    instance_id: 'test', job_id: null, mode: 'shadow', reason: '', note_ids: [] }) })))
  MockNoviClient.instances = []
  vi.mocked(fetchProjects).mockResolvedValue([])
  resetBootCache()
})

describe('generation ownership', () => {
  it('always creates a new conversation when sending from a project home', async () => {
    vi.mocked(fetchProjects).mockResolvedValue([{
      id: 'project-1', name: 'Project One', description: '', sharedContext: '',
      conversationIds: ['A'], sources: [{ root: 'C:\\project', capability: 'READ' }],
      createdAt: '', updatedAt: '',
    }])
    const { result } = renderChatHook()
    await waitFor(() => expect(result.current.chat.projects).toHaveLength(1))
    act(() => result.current.chat.setActiveId('A'))

    act(() => result.current.chat.sendMessage(
      'start a fresh project chat', undefined, undefined, 'project-1', true,
    ))

    await waitFor(() => expect(MockNoviClient.latest().sent).toHaveLength(1))
    const sent = MockNoviClient.latest().sent[0]
    expect(sent.conversationId).not.toBe('A')
    expect(result.current.chat.active).toMatchObject({
      id: sent.conversationId,
      projectId: 'project-1',
      sources: [{ root: 'C:\\project', capability: 'READ' }],
    })
    expect(findConv(result.current.chat.conversations, 'A')?.messages).toEqual([])
  })

  it('keeps landing-page sources temporary and promotes them before the first run', async () => {
    const fetchMock = vi.mocked(fetch)
    fetchMock.mockImplementation(async (input) => {
      const url = String(input)
      if (url.includes('/sources')) {
        return { ok: true, json: async () => ({ source: {
          root: 'C:\\work\\notes', capability: 'READ', hash: 'source-1',
        } }) } as Response
      }
      return { ok: true, json: async () => ({ state: 'idle', version: 0,
        instance_id: 'test', job_id: null, mode: 'shadow', reason: '', note_ids: [] }) } as Response
    })
    const { result } = renderChatHook()
    await waitFor(() => expect(result.current.chat.conversations).toHaveLength(2))

    await act(async () => {
      await result.current.chat.attachConversationSource('__draft__', 'C:\\work\\notes')
    })
    expect(result.current.chat.active.sources).toEqual([
      { root: 'C:\\work\\notes', capability: 'READ' },
    ])
    expect(fetchMock.mock.calls.some(([input]) => String(input).includes('/__draft__/sources'))).toBe(false)

    act(() => result.current.chat.sendMessage('summarize the notes'))
    await waitFor(() => expect(MockNoviClient.latest().sent).toHaveLength(1))
    const sent = MockNoviClient.latest().sent[0]
    expect(sent.conversationId).not.toBe('__draft__')
    expect(fetchMock.mock.calls.some(([input]) =>
      String(input).includes(`/api/conversations/${sent.conversationId}/sources`))).toBe(true)
    expect(result.current.chat.active.sources?.[0].root).toBe('C:\\work\\notes')
  })

  it('replaces a first-query fallback with Novi\'s generated title', async () => {
    const fetchMock = vi.mocked(fetch)
    fetchMock.mockImplementation(async (input) => {
      const url = String(input)
      if (url.endsWith('/api/conversations/A/title')) {
        return { ok: true, json: async () => ({ title: 'Dependency Map' }) } as Response
      }
      return { ok: true, json: async () => ({ state: 'idle', version: 0,
        instance_id: 'test', job_id: null, mode: 'shadow', reason: '', note_ids: [] }) } as Response
    })
    const { result } = renderChatHook()
    await waitFor(() => expect(result.current.chat.conversations).toHaveLength(2))
    act(() => result.current.chat.setActiveId('A'))
    act(() => result.current.chat.sendMessage('please summarize every dependency in this repository'))

    const client = MockNoviClient.latest()
    act(() => client.emitRun({ type: 'done' }))

    await waitFor(() => expect(findConv(result.current.chat.conversations, 'A')?.title)
      .toBe('Dependency Map'))
    expect(fetchMock.mock.calls.some(([input]) =>
      String(input).endsWith('/api/conversations/A/title'))).toBe(true)
  })

  it('shows current-protocol chunks before completion, including after context status events', async () => {
    const { result } = renderChatHook()
    await waitFor(() => expect(result.current.chat.conversations).toHaveLength(2))
    act(() => result.current.chat.setActiveId('A'))
    act(() => result.current.chat.sendMessage('hello'))
    const client = MockNoviClient.latest()
    const emit = (type: string, sequence: number, fields = {}) => act(() => client.emit({
      type, sequence, runId: 'stream-run', conversationId: 'A', ...fields,
    } as ServerEvent))
    emit('run_state', 1, { status: 'running' })
    emit('status', 2, { text: 'Compacting conversation context…' })
    emit('status', 3, { text: 'Conversation context compacted.' })
    emit('message_start', 4, { messageId: 'reply' })
    emit('token', 5, { messageId: 'reply', text: 'Hello **' })
    expect(result.current.chat.active.messages.slice(-1)[0]).toMatchObject({
      content: 'Hello **', streaming: true,
    })
    expect(result.current.chat.generating).toBe(true)
    act(() => result.current.chat.setActiveId('B'))
    emit('token', 6, { messageId: 'reply', text: 'world**' })
    expect(findConv(result.current.chat.conversations, 'A')?.messages.slice(-1)[0]).toMatchObject({
      content: 'Hello **world**', streaming: true,
    })
    expect(result.current.chat.active.messages).toHaveLength(0)
    emit('message_end', 7, { messageId: 'reply' })
    emit('done', 8)
    expect(findConv(result.current.chat.conversations, 'A')?.messages.slice(-1)[0]).toMatchObject({
      content: 'Hello **world**', streaming: false,
    })
  })

  it('routes streaming tokens to the conversation that started the generation, not the one on screen', async () => {
    const { result } = renderChatHook()

    await waitFor(() => expect(result.current.chat.conversations).toHaveLength(2))

    // 1. Start generation in conversation A.
    act(() => result.current.chat.setActiveId('A'))
    act(() => result.current.chat.sendMessage('hello from A'))
    expect(result.current.chat.active.id).toBe('A')
    expect(result.current.chat.generating).toBe(true)

    const client = MockNoviClient.latest()
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
    act(() => client.emitRun({ type: 'token', text: 'Hi ' }))
    act(() => client.emitRun({ type: 'token', text: 'there' }))

    // 4. Tokens must appear only in A.
    const convAAfter = findConv(result.current.chat.conversations, 'A')
    expect(convAAfter?.messages.some((m) => m.role === 'assistant' && m.content === 'Hi there')).toBe(true)

    // 5. B must remain unchanged.
    const convBAfter = findConv(result.current.chat.conversations, 'B')
    expect(convBAfter?.messages).toEqual([])

    // The trace panel for the on-screen conversation (B) must stay empty even
    // though a "thinking" step was pushed for the in-flight generation.
    act(() => client.emitRun({ type: 'tool_call', id: 'read-1', tool: 'read_file', args: { path: 'example.txt' } }))
    expect(result.current.chat.inlineSteps).toEqual([])

    // Switching back to A reveals the same generation state again — nothing
    // was lost or misrouted, it was just hidden while B was on screen.
    act(() => result.current.chat.setActiveId('A'))
    expect(result.current.chat.generating).toBe(true)
    expect(result.current.chat.inlineSteps.length).toBeGreaterThan(0)

    act(() => client.emitRun({ type: 'done' }))
    expect(result.current.chat.generating).toBe(false)
    expect(result.current.chat.busyReason).toBeNull()
    expect(result.current.chat.generatingConversationId).toBeNull()
  })

  it('refuses to start a second generation while one is already in flight (single-flight)', async () => {
    const { result } = renderChatHook()
    await waitFor(() => expect(result.current.chat.conversations).toHaveLength(2))

    act(() => result.current.chat.setActiveId('A'))
    act(() => result.current.chat.sendMessage('first'))
    const client = MockNoviClient.latest()
    expect(client.sent).toHaveLength(1)

    act(() => result.current.chat.setActiveId('B'))
    act(() => result.current.chat.sendMessage('second, from B, while A is busy'))
    // No second chat frame should have been sent — the backend is single-flight.
    expect(client.sent).toHaveLength(1)
    expect(findConv(result.current.chat.conversations, 'B')?.messages).toEqual([])
  })

  it('keeps the run active until an authoritative cancellation event arrives', async () => {
    const { result } = renderChatHook()
    await waitFor(() => expect(result.current.chat.conversations).toHaveLength(2))

    act(() => result.current.chat.setActiveId('A'))
    act(() => result.current.chat.sendMessage('hello'))
    expect(result.current.chat.generating).toBe(true)

    act(() => result.current.chat.stop())
    expect(result.current.chat.generating).toBe(true)

    act(() => MockNoviClient.latest().emitRun({ type: 'cancelled' }))
    expect(result.current.chat.generating).toBe(false)
  })

  it('reconstructs progressive messages from canonical replay events', async () => {
    const { result } = renderChatHook()
    await waitFor(() => expect(result.current.chat.conversations).toHaveLength(2))
    act(() => result.current.chat.setActiveId('A'))
    const client = MockNoviClient.latest()

    act(() => {
      client.emit({ type: 'run_state', runId: 'r1', conversationId: 'A', sequence: 1, status: 'running' })
      client.emit({ type: 'message_start', runId: 'r1', conversationId: 'A', sequence: 2, messageId: 'm1' })
      client.emit({ type: 'token', runId: 'r1', conversationId: 'A', sequence: 3, messageId: 'm1', text: 'First ' })
      client.emit({ type: 'token', runId: 'r1', conversationId: 'A', sequence: 4, messageId: 'm1', text: 'message' })
      client.emit({ type: 'message_end', runId: 'r1', conversationId: 'A', sequence: 5, messageId: 'm1' })
      client.emit({ type: 'done', runId: 'r1', conversationId: 'A', sequence: 6 })
    })

    const assistant = findConv(result.current.chat.conversations, 'A')?.messages.find(message => message.id === 'm1')
    expect(assistant?.content).toBe('First message')
    expect(assistant?.streaming).toBe(false)
    expect(result.current.chat.generating).toBe(false)
  })
})

describe('cross-conversation notifications', () => {  it('pushes a notification when a response finishes off-screen, not when it finishes on-screen', async () => {
    const { result } = renderChatHook()
    await waitFor(() => expect(result.current.chat.conversations).toHaveLength(2))

    act(() => result.current.chat.setActiveId('A'))
    act(() => result.current.chat.sendMessage('hello from A'))
    const client = MockNoviClient.latest()

    // Switch away before it finishes.
    act(() => result.current.chat.setActiveId('B'))
    act(() => client.emitRun({ type: 'done' }))

    expect(result.current.notifications.notifications).toHaveLength(1)
    expect(result.current.notifications.notifications[0]).toMatchObject({ conversationId: 'A', severity: 'success' })

    // Now do the same but stay on the conversation that's generating — no notification expected.
    act(() => result.current.chat.setActiveId('B'))
    act(() => result.current.chat.sendMessage('hello from B'))
    act(() => client.emitRun({ type: 'done' }))
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
    const client = MockNoviClient.latest()
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
    expect(MockNoviClient.latest().sent[0].deepResearch).toBeUndefined()
  })
})

describe('agent phase activity (Phase 8G)', () => {
  it('maps research phase events to user-facing labels without graph topology', async () => {
    const { result } = renderChatHook()
    await waitFor(() => expect(result.current.chat.conversations).toHaveLength(2))
    act(() => result.current.chat.setActiveId('A'))
    act(() => result.current.chat.sendMessage('research something'))
    const client = MockNoviClient.latest()

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
    const client = MockNoviClient.latest()

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
  it('streams reasoning separately and preserves it when answer tokens arrive', async () => {
    const { result } = renderChatHook()
    await waitFor(() => expect(result.current.chat.conversations).toHaveLength(2))

    act(() => result.current.chat.setActiveId('A'))
    act(() => result.current.chat.sendMessage('hard problem'))
    const client = MockNoviClient.latest()

    act(() => client.emitRun({ type: 'reasoning', text: 'step one ' }))
    act(() => client.emitRun({ type: 'reasoning', text: 'step two' }))
    act(() => client.emitRun({ type: 'token', text: 'Answer' }))

    const assistant = findConv(result.current.chat.conversations, 'A')?.messages.find((m) => m.role === 'assistant')
    expect(assistant?.content).toBe('Answer')
    expect(assistant?.thought).toBe('step one step two')

    // The trace is drained — subsequent tokens don't re-append it.
    act(() => client.emitRun({ type: 'token', text: ' extended' }))
    const after = findConv(result.current.chat.conversations, 'A')?.messages.find((m) => m.role === 'assistant')
    expect(after?.content).toBe('Answer extended')
    expect(after?.thought).toBe('step one step two')
  })

  it('exposes a live thinking trace while reasoning is in flight', async () => {
    const { result } = renderChatHook()
    await waitFor(() => expect(result.current.chat.conversations).toHaveLength(2))

    act(() => result.current.chat.setActiveId('A'))
    act(() => result.current.chat.sendMessage('hard problem'))
    const client = MockNoviClient.latest()

    expect(result.current.chat.thinking).toBe(false)
    expect(result.current.chat.liveThought).toBe('')

    act(() => client.emitRun({ type: 'reasoning', text: 'step one ' }))
    expect(result.current.chat.thinking).toBe(true)
    expect(result.current.chat.liveThought).toBe('step one ')

    act(() => client.emitRun({ type: 'reasoning', text: 'step two' }))
    expect(result.current.chat.liveThought).toBe('step one step two')

    // The first answer token ends thinking; the trace stays above the answer.
    act(() => client.emitRun({ type: 'token', text: 'Answer' }))
    expect(result.current.chat.thinking).toBe(false)
    expect(result.current.chat.liveThought).toBe('')
  })

  it('assistant messages without reasoning carry no thought block', async () => {
    const { result } = renderChatHook()
    await waitFor(() => expect(result.current.chat.conversations).toHaveLength(2))

    act(() => result.current.chat.setActiveId('A'))
    act(() => result.current.chat.sendMessage('simple'))
    act(() => MockNoviClient.latest().emitRun({ type: 'token', text: 'Hi' }))

    const assistant = findConv(result.current.chat.conversations, 'A')?.messages.find((m) => m.role === 'assistant')
    expect(assistant?.content).toBe('Hi')
    expect(assistant?.thought).toBeUndefined()
  })
})
