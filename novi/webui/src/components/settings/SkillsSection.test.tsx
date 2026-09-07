import { describe, it, expect, vi } from 'vitest'
import { render, screen, fireEvent, waitFor } from '@testing-library/react'
import { SkillsSection } from './SkillsSection'

const showError = vi.fn()

vi.mock('@/hooks/useToast', () => ({
  useToast: () => ({ showError, showSuccess: vi.fn(), showInfo: vi.fn() }),
}))

vi.mock('@/hooks/useConfirm', () => ({
  useConfirm: () => ({ confirm: vi.fn().mockResolvedValue(true), dialog: null }),
}))

vi.mock('@/services/novi', () => ({
  uploadSkill: vi.fn(),
  createSkill: vi.fn(),
  deleteSkill: vi.fn(),
}))

import { createSkill } from '@/services/novi'

describe('SkillsSection handleWriteSubmit (Task 6)', () => {
  it('surfaces the server error message when createSkill rejects', async () => {
    vi.mocked(createSkill).mockRejectedValueOnce(new Error('invalid name'))
    showError.mockClear()

    render(<SkillsSection skills={[]} onRefresh={vi.fn()} onClose={vi.fn()} />)

    fireEvent.click(screen.getByRole('button', { name: /Add/i }))
    fireEvent.click(screen.getByRole('button', { name: /Write instructions/i }))

    fireEvent.change(screen.getByPlaceholderText('Skill name'), {
      target: { value: 'bad name!' },
    })
    fireEvent.change(screen.getByPlaceholderText('Short description (optional)'), {
      target: { value: 'desc' },
    })
    fireEvent.change(screen.getByPlaceholderText('Skill instructions in Markdown...'), {
      target: { value: '# content' },
    })

    fireEvent.click(screen.getByRole('button', { name: /Save skill/i }))

    await waitFor(() => expect(showError).toHaveBeenCalledTimes(1))
    expect(showError.mock.calls[0][0]).toContain('invalid name')
  })
})
