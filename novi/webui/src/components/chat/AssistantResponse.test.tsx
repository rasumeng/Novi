import { describe, it, expect } from 'vitest'
import { render } from '@testing-library/react'
import { ToolTrace, WaitingBubble } from './AssistantResponse'
import type { ProjectedTool } from '../../state/runReducer'

function tool(overrides: Partial<ProjectedTool> = {}): ProjectedTool {
  return {
    id: 't1',
    name: 'fetch_url',
    arguments: { url: 'https://www.weather.gov/fwd/', max_length: 500 },
    result: 'A slow moving front will be the focus area.',
    status: 'succeeded',
    afterMessageId: null,
    ...overrides,
  } as ProjectedTool
}

describe('ToolTrace — tool arguments', () => {
  it('renders the arguments a tool was called with', () => {
    const { container } = render(<ToolTrace tool={tool()} />)
    // Verifies the args Novi sent over the wire actually reach the DOM.
    expect(container.innerHTML).toContain('weather.gov/fwd')
    expect(container.innerHTML).toContain('max_length')
  })

  it('labels a completed tool as Ran', () => {
    const { container } = render(<ToolTrace tool={tool()} />)
    expect(container.textContent).toContain('Ran fetch url')
  })

  it('keeps the arguments reachable in the DOM rather than dropping them', () => {
    // Regression guard: args used to be silently lost for some tools.
    const { container } = render(<ToolTrace tool={tool({ arguments: { url: 'https://example.com/x' } })} />)
    expect(container.textContent).toContain('https://example.com/x')
  })

  it('renders without arguments', () => {
    const { container } = render(<ToolTrace tool={tool({ arguments: {} })} />)
    expect(container.textContent).toContain('Ran fetch url')
  })
})
describe('WaitingBubble - liveness while the model is slow', () => {
  it('renders exactly three animated dots as direct children', () => {
    const { container } = render(<WaitingBubble />)
    const bubble = container.querySelector('.novi-waiting')
    expect(bubble).not.toBeNull()
    // The dots must BE the direct children: .novi-waiting > span styles them.
    const dots = Array.from(bubble!.children)
    expect(dots.length).toBe(3)
    expect(dots.every(d => d.tagName === 'SPAN')).toBe(true)
    // Staggered so the pulse reads as a travelling dot.
    expect(dots.map(d => (d as HTMLElement).style.animationDelay))
      .toEqual(['0ms', '180ms', '360ms'])
  })

  it('shows no timer or numeric chrome', () => {
    const { container } = render(<WaitingBubble />)
    expect(container.querySelector('[data-testid="waiting-elapsed"]')).toBeNull()
    expect(container.textContent).toBe('')
  })
})
