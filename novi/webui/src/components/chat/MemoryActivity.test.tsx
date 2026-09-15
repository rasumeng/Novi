import { render, screen, fireEvent } from '@testing-library/react'
import { vi, describe, it, expect, beforeEach } from 'vitest'
import { MemoryActivity } from './MemoryActivity'
import type { MemoryActivityState } from '@/services/novi'

const { info } = vi.hoisted(() => ({ info: vi.fn() }))
vi.mock('@/hooks/useToast', () => ({ useToast: () => ({ showInfo: info, showError: vi.fn() }) }))
const activity: MemoryActivityState = { state: 'proposing', instance_id: 'one', version: 1,
  job_id: 'job', mode: 'shadow', reason: '', note_ids: [] }

describe('memory activity transparency', () => {
  beforeEach(() => {
    info.mockClear()
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue({ ok: true, json: async () => [] }))
  })
  it('announces suggestions honestly and keeps typing usable', () => {
    const { rerender } = render(<><input aria-label="Message" /><MemoryActivity activity={activity} /></>)
    expect(screen.getByRole('status').textContent).toBe('Consolidating')
    expect(screen.getByText('Reading the conversation')).toBeTruthy()
    const input = screen.getByLabelText('Message')
    fireEvent.change(input, { target: { value: 'New question' } })
    expect((input as HTMLInputElement).value).toBe('New question')
    const count = info.mock.calls.length
    rerender(<><input aria-label="Message" /><MemoryActivity activity={{ ...activity, state: 'verifying', version: 2 }} /></>)
    expect(info.mock.calls.length).toBe(count)
    expect(screen.queryByRole('dialog')).toBeNull()
  })
  it('only reports saved changes for applied state', () => {
    render(<MemoryActivity activity={{ ...activity, mode: 'apply', state: 'applied', note_ids: ['n1'] }} />)
    expect(screen.getByRole('status').textContent).toBe('Updated 1 memory')
    expect(screen.getByText('Recent consolidations')).toBeTruthy()
  })
  it('distinguishes updates being off from an unavailable configuration', () => {
    const { rerender } = render(<MemoryActivity activity={{ ...activity, state: 'disabled',
      reason: 'Permitted turns remain pending' }} />)
    expect(screen.getByRole('status').textContent).toBe('Automatic saving is off')
    expect(screen.getByText('Permitted turns remain pending')).toBeTruthy()
    rerender(<MemoryActivity activity={{ ...activity, state: 'unavailable', reason: 'Selected provider unsupported' }} />)
    expect(screen.getByRole('status').textContent).toBe('Automatic saving unavailable')
  })
})
