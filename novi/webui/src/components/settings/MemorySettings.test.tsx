import { fireEvent, render, screen } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import { MemorySettings } from './MemorySettings'

vi.mock('@/hooks/useToast', () => ({ useToast: () => ({ showError: vi.fn() }) }))
vi.mock('@/hooks/useConfirm', () => ({ useConfirm: () => ({ confirm: vi.fn(), dialog: null }) }))
vi.mock('@/components/knowledge/KnowledgeOverview', () => ({ KnowledgeOverview: () => null }))

describe('automatic memory setting', () => {
  it('uses the existing persisted settings flow and respects the main memory switch', () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue({ json: async () => ({ data: [], status: 'ok' }) }))
    const set = vi.fn().mockResolvedValue(true)
    const framework = { values: { 'memory.enabled': true, 'memory.automatic_updates': true }, set }
    const { rerender } = render(<MemorySettings framework={framework as any} />)
    const toggle = screen.getByRole('switch', { name: 'Save memories automatically' })
    expect(toggle.getAttribute('aria-checked')).toBe('true')
    fireEvent.click(toggle)
    expect(set).toHaveBeenCalledWith('memory.automatic_updates', false)

    rerender(<MemorySettings framework={{ ...framework, values: { ...framework.values, 'memory.enabled': false } } as any} />)
    expect(screen.getByRole('switch', { name: 'Save memories automatically' }).hasAttribute('disabled')).toBe(true)
  })
})
