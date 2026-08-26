import { describe, it, expect, vi } from 'vitest'
import { render, screen, waitFor } from '@testing-library/react'
import { AgentSettings } from './AgentSettings'
import type { SettingsData } from './types'

vi.mock('@/services/novi', () => ({
  fetchKnowledgeOverview: vi.fn().mockResolvedValue({
    categories: [
      { category: 'identity', label: 'About you', entries: [{ content: 'Prefers concise answers', evidence: 'Confirmed by repeated agreement' }] },
      { category: 'preference', label: 'Preferences', entries: [{ content: 'Likes local-first tools', evidence: 'Candidate' }] },
    ],
    total: 2,
    updated: '',
  }),
}))

function makeConfig(over: Partial<SettingsData> = {}): SettingsData {
  return {
    llm: {},
    models: { agent: 'llama3.1:8b' },
    agent: { system_prompt: 'Be brief', max_steps: 8, temperature: 0.3 },
    ...over,
  }
}

describe('AgentSettings (M4.2a)', () => {
  it('shows identity/context projection from the Brain, not a personality selector', async () => {
    render(<AgentSettings config={makeConfig()} setConfig={vi.fn()} setDirty={vi.fn()} />)
    await waitFor(() => expect(screen.getByText('What Novi knows about you')).toBeTruthy())
    expect(screen.getByText('Prefers concise answers')).toBeTruthy()
    // No personality controls allowed.
    expect(screen.queryByText(/personality/i)).toBeNull()
    expect(screen.queryByRole('button', { name: /select personality/i })).toBeNull()
    expect(screen.queryByRole('combobox')).toBeNull()
  })

  it('keeps autonomy/behavior controls (system prompt, max steps, temperature)', () => {
    render(<AgentSettings config={makeConfig()} setConfig={vi.fn()} setDirty={vi.fn()} />)
    expect(screen.getByText('Extra instructions')).toBeTruthy()
    expect(screen.getByText('Max steps')).toBeTruthy()
    expect(screen.getByText('Temperature')).toBeTruthy()
    expect((screen.getByDisplayValue('Be brief') as HTMLTextAreaElement).value).toBe('Be brief')
  })

  it('shows the effective model as read-only reference', () => {
    render(<AgentSettings config={makeConfig()} setConfig={vi.fn()} setDirty={vi.fn()} />)
    expect(screen.getByText('Effective model')).toBeTruthy()
    expect(screen.getByText('llama3.1:8b')).toBeTruthy()
  })
})
