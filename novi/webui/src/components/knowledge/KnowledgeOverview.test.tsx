import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, fireEvent, waitFor } from '@testing-library/react'

vi.mock('@/services/novi', () => ({
  fetchKnowledgeOverview: vi.fn(),
}))

import { fetchKnowledgeOverview } from '@/services/novi'
import { KnowledgeOverview } from './KnowledgeOverview'

describe('KnowledgeOverview consistent states', () => {
  beforeEach(() => vi.clearAllMocks())

  it('shows loading skeleton initially', async () => {
    const never = new Promise(() => {})
    vi.mocked(fetchKnowledgeOverview).mockReturnValue(never as any)
    const { container } = render(<KnowledgeOverview />)
    expect(container.querySelector('.animate-shimmer')).toBeTruthy()
  })

  it('shows error banner with Retry and retries to loading', async () => {
    vi.mocked(fetchKnowledgeOverview).mockRejectedValueOnce(new Error('down'))
    render(<KnowledgeOverview />)
    await waitFor(() => expect(screen.getAllByText('Brain store unavailable — check logs').length).toBeGreaterThan(0))
    expect(screen.getByText('Retry')).toBeTruthy()

    // second call succeeds with empty -> empty state
    vi.mocked(fetchKnowledgeOverview).mockResolvedValueOnce({ categories: [], total: 0, updated: '' } as any)
    fireEvent.click(screen.getByText('Retry'))
    // immediately shows loading skeleton again
    expect(document.querySelector('.animate-shimmer')).toBeTruthy()
    await waitFor(() => expect(screen.getByText('No knowledge yet — start a conversation')).toBeTruthy())
  })

  it('shows empty state when no categories', async () => {
    vi.mocked(fetchKnowledgeOverview).mockResolvedValue({ categories: [], total: 0, updated: '' } as any)
    render(<KnowledgeOverview />)
    await waitFor(() => expect(screen.getByText('No knowledge yet — start a conversation')).toBeTruthy())
  })
})
