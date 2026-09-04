import { describe, it, expect, vi } from 'vitest'
import { render, screen, fireEvent } from '@testing-library/react'
import { ProjectsPanel } from './ProjectsPanel'

const baseProps = {
  projects: [] as any[],
  conversations: [] as any[],
  onCreateProject: vi.fn(async () => null),
  onUpdateProject: vi.fn(async () => null),
  onDeleteProject: vi.fn(),
  onSelectConversation: vi.fn(),
  onRemoveConversation: vi.fn(),
  onSelectProject: vi.fn(),
}

describe('ProjectsPanel consistent states', () => {
  it('renders loading skeleton when loading', () => {
    const { container } = render(<ProjectsPanel {...baseProps} loading />)
    expect(container.querySelector('.animate-shimmer')).toBeTruthy()
  })

  it('renders error banner with retry', () => {
    const onRetry = vi.fn()
    render(<ProjectsPanel {...baseProps} error="Projects fetch failed" onRetry={onRetry} />)
    expect(screen.getByText(/Projects fetch failed/)).toBeTruthy()
    fireEvent.click(screen.getByText('Retry'))
    expect(onRetry).toHaveBeenCalledTimes(1)
  })

  it('renders empty state when no projects', () => {
    render(<ProjectsPanel {...baseProps} />)
    expect(screen.getByText('No projects yet')).toBeTruthy()
  })

  it('error → retry transitions to loading', () => {
    const { rerender, container } = render(<ProjectsPanel {...baseProps} error="oops" onRetry={vi.fn()} />)
    expect(screen.getByText('Retry')).toBeTruthy()
    rerender(<ProjectsPanel {...baseProps} loading />)
    expect(container.querySelector('.animate-shimmer')).toBeTruthy()
  })
})
