import { describe, expect, it } from 'vitest'
import { render } from '@testing-library/react'
import { LoadingSkeleton } from './LoadingSkeleton'

describe('LoadingSkeleton', () => {
  it('staggers each row to create a loading ripple', () => {
    const { container } = render(<LoadingSkeleton rows={4} compact />)
    const rows = Array.from(container.querySelectorAll<HTMLElement>('.novi-loading-skeleton'))

    expect(rows).toHaveLength(4)
    expect(rows.map((row) => row.style.animationDelay)).toEqual([
      '0ms',
      '140ms',
      '280ms',
      '420ms',
    ])
  })
})
