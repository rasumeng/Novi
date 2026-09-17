import { describe, expect, it } from 'vitest'
import { createRunProjection, reduceRunEvent, type RunWireEvent } from './runReducer'

const ev = (type: RunWireEvent['type'], sequence: number, extra: Record<string, unknown> = {}) => ({
  type, runId: 'r1', conversationId: 'c1', sequence, ...extra,
}) as RunWireEvent

describe('runReducer', () => {
  it('keeps progressive messages separate and terminalizes only the run event', () => {
    let state = createRunProjection('c1')
    state = reduceRunEvent(state, ev('run_state', 1, { status: 'running' }))
    state = reduceRunEvent(state, ev('message_start', 2, { messageId: 'm1' }))
    state = reduceRunEvent(state, ev('token', 3, { messageId: 'm1', text: 'First' }))
    state = reduceRunEvent(state, ev('message_end', 4, { messageId: 'm1' }))
    state = reduceRunEvent(state, ev('message_start', 5, { messageId: 'm2' }))
    state = reduceRunEvent(state, ev('token', 6, { messageId: 'm2', text: 'Second' }))

    expect(state.status).toBe('running')
    expect(state.messageOrder).toEqual(['m1', 'm2'])
    expect(state.messages.m1.status).toBe('completed')
    expect(state.messages.m2.status).toBe('streaming')

    state = reduceRunEvent(state, ev('done', 7))
    expect(state.status).toBe('completed')
    expect(state.messages.m2.status).toBe('completed')
  })

  it('keys same-name tool calls by call id and preserves failure status', () => {
    let state = reduceRunEvent(createRunProjection('c1'), ev('run_state', 1, { status: 'running' }))
    state = reduceRunEvent(state, ev('tool_call', 2, { id: 'a', tool: 'read_file', args: { path: 'a' } }))
    state = reduceRunEvent(state, ev('tool_call', 3, { id: 'b', tool: 'read_file', args: { path: 'b' } }))
    state = reduceRunEvent(state, ev('tool_result', 4, { id: 'b', tool: 'read_file', result: 'missing', status: 'failed' }))

    expect(state.toolOrder).toEqual(['a', 'b'])
    expect(state.tools.a.status).toBe('requested')
    expect(state.tools.b.status).toBe('failed')
  })

  it('ignores duplicates and foreign runs and requests recovery for gaps', () => {
    let state = reduceRunEvent(createRunProjection('c1'), ev('run_state', 1, { status: 'running' }))
    state = reduceRunEvent(state, ev('message_start', 2, { messageId: 'm1' }))
    const duplicate = reduceRunEvent(state, ev('message_start', 2, { messageId: 'other' }))
    const foreign = reduceRunEvent(state, { ...ev('message_start', 3, { messageId: 'other' }), runId: 'r2' })
    const gap = reduceRunEvent(state, ev('token', 4, { messageId: 'm1', text: 'late' }))

    expect(duplicate).toBe(state)
    expect(foreign).toBe(state)
    expect(gap.recoveryAfter).toBe(2)
    expect(gap.messages.m1.content).toBe('')
  })

  it('keeps permission pending after progress and settles it on cancellation', () => {
    let state = reduceRunEvent(createRunProjection('c1'), ev('run_state', 1, { status: 'running' }))
    state = reduceRunEvent(state, ev('permission_request', 2, {
      id: 'p1', tool: 'write_file', args: { path: 'a' }, effects: ['write'], expiresAt: 'later',
    }))
    state = reduceRunEvent(state, ev('message_start', 3, { messageId: 'm1' }))
    state = reduceRunEvent(state, ev('token', 4, { messageId: 'm1', text: 'Waiting for approval' }))
    expect(state.permission?.status).toBe('pending')

    state = reduceRunEvent(state, ev('cancelled', 5))
    expect(state.status).toBe('cancelled')
    expect(state.permission?.status).toBe('cancelled')
  })

  it('starts a new projection when the same conversation begins another run', () => {
    let state = reduceRunEvent(createRunProjection('c1'), ev('run_state', 1, { status: 'running' }))
    state = reduceRunEvent(state, ev('done', 2))
    const nextRun = { ...ev('run_state', 1, { status: 'running' }), runId: 'r2' }

    state = reduceRunEvent(state, nextRun)

    expect(state.runId).toBe('r2')
    expect(state.status).toBe('running')
    expect(state.lastSequence).toBe(1)
  })
})
