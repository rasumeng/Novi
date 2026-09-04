import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, fireEvent, waitFor } from '@testing-library/react'
import { SearchModal } from './SearchModal'

describe('SearchModal consistent states', () => {
  beforeEach(() => {
    vi.restoreAllMocks()
  })

  it('shows retry button on error and retries', async () => {
    let callCount = 0
    vi.stubGlobal('fetch', vi.fn(async () => {
      callCount++
      if (callCount === 1) throw new Error('network down')
      return { ok: true, json: async () => [{ id: '1', title: 'Found', pinned: false, match: 'hit' }] } as any
    }))
    render(<SearchModal open onClose={vi.fn()} onSelect={vi.fn()} />)
    const input = screen.getByPlaceholderText('Search chats, tasks, sessions...')
    fireEvent.change(input, { target: { value: 'hello' } })
    await waitFor(() => expect(screen.getByText("Search couldn't be completed")).toBeTruthy(), { timeout: 2000 })
    expect(screen.getByText('Retry')).toBeTruthy()
    fireEvent.click(screen.getByText('Retry'))
    await waitFor(() => expect(screen.getByText('Found')).toBeTruthy(), { timeout: 2000 })
    expect(callCount).toBe(2)
  })

  it('shows empty when no matches', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => ({ ok: true, json: async () => [] } as any)))
    render(<SearchModal open onClose={vi.fn()} onSelect={vi.fn()} />)
    const input = screen.getByPlaceholderText('Search chats, tasks, sessions...')
    fireEvent.change(input, { target: { value: 'nomatch' } })
    await waitFor(() => expect(screen.getByText('No matches')).toBeTruthy(), { timeout: 2000 })
  })
})
