import { describe, it, expect, vi } from 'vitest'
import { render, screen, fireEvent } from '@testing-library/react'
import { JobsPage } from './JobsPage'

describe('JobsPage consistent states', () => {
  it('renders loading skeleton when loading', () => {
    const { container } = render(<JobsPage runs={[]} onStart={vi.fn()} onStop={vi.fn()} onRefresh={vi.fn()} loading />)
    // LoadingSkeleton renders shimmer divs
    expect(container.querySelector('.animate-shimmer')).toBeTruthy()
  })

  it('renders error banner with Retry', () => {
    const onRefresh = vi.fn()
    render(<JobsPage runs={[]} onStart={vi.fn()} onStop={vi.fn()} onRefresh={onRefresh} error="Jobs fetch failed" />)
    expect(screen.getByText(/Jobs fetch failed/)).toBeTruthy()
    const btn = screen.getByText('Retry')
    fireEvent.click(btn)
    expect(onRefresh).toHaveBeenCalledTimes(1)
  })

  it('renders empty state when no jobs and no error/loading', () => {
    render(<JobsPage runs={[]} onStart={vi.fn()} onStop={vi.fn()} onRefresh={vi.fn()} />)
    expect(screen.getByText('No jobs yet')).toBeTruthy()
  })

  it('error → retry shows loading on next render', () => {
    const { rerender } = render(<JobsPage runs={[]} onStart={vi.fn()} onStop={vi.fn()} onRefresh={vi.fn()} error="oops" />)
    expect(screen.getByText('Retry')).toBeTruthy()
    rerender(<JobsPage runs={[]} onStart={vi.fn()} onStop={vi.fn()} onRefresh={vi.fn()} loading />)
    expect(document.querySelector('.animate-shimmer')).toBeTruthy()
  })
})
