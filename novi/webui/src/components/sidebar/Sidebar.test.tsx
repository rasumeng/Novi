import { describe, it, expect } from 'vitest'
import { NAV_ORDER } from './workspaceModes'

describe('beta: jobs hidden', () => {
  it('jobs hidden from NAV_ORDER', () => {
    expect(NAV_ORDER).not.toContain('jobs')
  })
})
