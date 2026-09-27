import { it, expect, vi, afterEach } from 'vitest'
import { render, screen, cleanup, fireEvent } from '@testing-library/react'
import { Conversation } from './Conversation'
import { createRunProjection, reduceRunEvent, type RunWireEvent } from '@/state/runReducer'
import type { ChatMessage } from '@/types'

vi.mock('./PromptInput', () => ({ PromptInput: () => <textarea aria-label="Message Novi" /> }))
vi.mock('./LandingPage', () => ({ LandingPage: () => null }))
afterEach(cleanup)

const user: ChatMessage = { id: 'u', role: 'user', content: '**A question** with `code`', createdAt: 'Now' }
const defaults = {
  connection: 'open' as const, generating: true, plan: null, permission: null,
  activeProject: null, backgroundRuns: [],
  onSend: vi.fn(), onStop: vi.fn(), onApprovePlan: vi.fn(), onRejectPlan: vi.fn(), onAnswerPermission: vi.fn(),
}

it('replaces the waiting dots with live reasoning and then an unboxed Markdown answer', () => {
  let run = createRunProjection('c')
  const event = (type: RunWireEvent['type'], fields = {}) => {
    run = reduceRunEvent(run, { type, runId: 'r', conversationId: 'c', sequence: run.lastSequence + 1, ...fields })
  }
  const view = () => <Conversation {...defaults} generating={run.status !== 'completed'} runProjection={run}
    conversation={{ id: 'c', title: 'Chat', pinned: false, updatedAt: '', messages: [user,
      ...run.messageOrder.map(id => ({ id, role: 'assistant' as const, content: run.messages[id].content,
        thought: run.messages[id].thought, streaming: run.messages[id].status === 'streaming', createdAt: 'Now' }))] }} />
  event('run_state', { status: 'running' })
  const { rerender, container } = render(view())
  expect(screen.getByTestId('waiting-bubble')).toBeTruthy()
  expect(screen.queryByText(/Waiting for response|Activity/)).toBeNull()
  expect(container.querySelector('[data-testid="user-message"] strong')?.textContent).toBe('A question')
  expect(container.querySelector('[data-testid="user-message"] code')?.textContent).toBe('code')

  event('message_start', { messageId: 'm' })
  event('reasoning', { messageId: 'm', text: 'Let me **check**.' })
  rerender(view())
  expect(screen.queryByTestId('waiting-bubble')).toBeNull()
  expect(screen.getByText('Thinking')).toBeTruthy()
  expect(screen.getByRole('status').getAttribute('aria-expanded')).toBe('true')
  expect(container.querySelector('[data-testid="reasoning"] strong')?.textContent).toBe('check')
  expect(container.querySelector('[data-testid="reasoning"] .novi-markdown')?.className).toContain('[&_p]:!text-base-500')

  event('token', { messageId: 'm', text: 'The **answer' })
  rerender(view())
  expect(screen.queryByText('Thinking')).toBeNull()
  expect(screen.getByText('Thoughts')).toBeTruthy()
  expect(screen.getByRole('button', { name: 'Thoughts' }).getAttribute('aria-expanded')).toBe('false')
  fireEvent.click(screen.getByText('Thoughts'))
  expect(screen.getByRole('button', { name: 'Thoughts' }).getAttribute('aria-expanded')).toBe('true')
  expect(screen.getByTestId('reasoning').className).toContain('text-base-500')
  expect(screen.queryByTestId('waiting-bubble')).toBeNull()
  expect(screen.getByTestId('assistant-response').className).not.toMatch(/rounded|bg-/)
  expect(screen.getByTestId('assistant-response').textContent).toContain('The **answer')
  event('token', { messageId: 'm', text: '**.' })
  rerender(view())
  expect(screen.getByText('answer').tagName).toBe('STRONG')
  event('done')
  rerender(view())
  expect(screen.queryByTestId('waiting-bubble')).toBeNull()
  expect(screen.getByText('answer')).toBeTruthy()
})

it('removes waiting for tool-only activity', () => {
  let run = reduceRunEvent(createRunProjection('c'), {
    type: 'run_state', runId: 'r', conversationId: 'c', sequence: 1, status: 'running',
  })
  run = reduceRunEvent(run, { type: 'tool_call', runId: 'r', conversationId: 'c', sequence: 2,
    id: 't', tool: 'read_file', args: { path: 'example.txt' } })
  render(<Conversation {...defaults} runProjection={run}
    conversation={{ id: 'c', title: 'Chat', pinned: false, updatedAt: '', messages: [user] }} />)
  expect(screen.queryByTestId('waiting-bubble')).toBeNull()
  expect(screen.getByText('Running read file…')).toBeTruthy()
})
