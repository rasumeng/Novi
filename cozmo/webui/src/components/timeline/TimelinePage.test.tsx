import { describe, it, expect, vi } from 'vitest'
import { render, screen, fireEvent } from '@testing-library/react'
import { TimelinePage } from './TimelinePage'
import type { TimelineEntry } from '@/types'

const entry = (over: Partial<TimelineEntry>): TimelineEntry => ({
  id: 'row-1',
  kind: 'conversation.observed',
  title: 'Title',
  detail: 'Detail',
  timestamp: '2026-08-05T09:00:00Z',
  ...over,
})

describe('TimelinePage', () => {
  it('shows an empty state when there are no entries', () => {
    render(<TimelinePage entries={[]} onRefresh={vi.fn()} />)
    expect(screen.getByText('No activity yet')).toBeTruthy()
  })

  it('opens a conversation when a row with a conversation_id is clicked', () => {
    const open = vi.fn()
    render(
      <TimelinePage
        entries={[entry({ id: 'row-1', conversation_id: 'conv-9', title: 'Did things' })]}
        onRefresh={vi.fn()}
        onOpenConversation={open}
      />
    )
    expect(screen.getByText('Did things')).toBeTruthy()
    fireEvent.click(screen.getByLabelText(/Open conversation/))
    expect(open).toHaveBeenCalledWith('conv-9')
  })

  it('does not treat rows without a conversation_id as clickable', () => {
    const open = vi.fn()
    render(
      <TimelinePage
        entries={[entry({ id: 'row-1', title: 'Learned something' })]}
        onRefresh={vi.fn()}
        onOpenConversation={open}
      />
    )
    expect(screen.queryByLabelText(/Open conversation/)).toBeNull()
    expect(open).not.toHaveBeenCalled()
  })
})