import { render, screen } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import { TitleBar } from './TitleBar'

vi.mock('./WindowControls', () => ({ WindowControls: () => null }))
vi.mock('@/components/chat/NotificationBell', () => ({ NotificationBell: () => null }))

describe('TitleBar memory status', () => {
  it('shows Consolidating in the shared status area while memory is running', () => {
    render(<TitleBar connection="open" workingActivityTitle="Current chat" isActiveConversation
      memoryActivity={{ state: 'verifying', version: 2, instance_id: 'i', job_id: 'j', mode: 'apply', reason: '', note_ids: [] }} />)
    expect(screen.getByRole('status').textContent).toBe('Consolidating')
    expect(screen.queryByText('Responding')).toBeNull()
  })
})
