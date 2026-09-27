import { render, screen } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import { TitleBar } from './TitleBar'

vi.mock('./WindowControls', () => ({ WindowControls: () => null }))
vi.mock('@/components/chat/NotificationBell', () => ({ NotificationBell: () => null }))

describe('TitleBar', () => {
  it('keeps generation and memory activity out of the application chrome', () => {
    render(<TitleBar connection="open" />)

    expect(screen.getByRole('button', { name: 'Collapse sidebar' })).toBeTruthy()
    expect(screen.getByRole('button', { name: 'Search' })).toBeTruthy()
    expect(screen.getByRole('button', { name: 'Settings' })).toBeTruthy()
    expect(screen.queryByText('Responding')).toBeNull()
    expect(screen.queryByText('Consolidating')).toBeNull()
  })
})
