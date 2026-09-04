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

describe('TimelinePage consistent states', () => {
  it('shows loading skeleton when loading', () => {
    const { container } = render(<TimelinePage entries={[]} onRefresh={vi.fn()} loading />)
    expect(container.querySelector('.animate-shimmer')).toBeTruthy()
  })

  it('shows error banner with Retry and calls onRefresh', () => {
    const onRefresh = vi.fn()
    render(<TimelinePage entries={[]} onRefresh={onRefresh} error="Brain store unavailable — check logs" status="unavailable" />)
    expect(screen.getAllByText('Brain store unavailable — check logs').length).toBeGreaterThan(0)
    fireEvent.click(screen.getByText('Retry'))
    expect(onRefresh).toHaveBeenCalledTimes(1)
  })

  it('error → retry shows loading', () => {
    const { rerender, container } = render(<TimelinePage entries={[]} onRefresh={vi.fn()} error="oops" status="unavailable" />)
    expect(screen.getByText('Retry')).toBeTruthy()
    rerender(<TimelinePage entries={[]} onRefresh={vi.fn()} loading />)
    expect(container.querySelector('.animate-shimmer')).toBeTruthy()
  })
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