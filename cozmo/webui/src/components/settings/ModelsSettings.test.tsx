import { describe, it, expect, vi } from 'vitest'
import { render, screen, fireEvent } from '@testing-library/react'
import { ModelsSettings } from './ModelsSettings'
import type { DiscoveryPayload } from './api'

const DISCOVERY: DiscoveryPayload = {
  hardware: { ramGb: 16 },
  models: [
    { name: 'llama3.1:8b', displayName: 'Llama 3.1 8B', status: 'installed', size: null, capabilities: {}, recommended: true, tier: 'supported', reasons: ['Tested with Cozmo'], approxRamGb: 5 },
    { name: 'qwen2.5-coder:7b', displayName: 'Qwen 2.5 Coder 7B', status: 'installed', size: null, capabilities: {}, recommended: true, tier: 'supported', reasons: [], approxRamGb: 6 },
    { name: 'nomic-embed-text', displayName: 'Nomic Embed Text', status: 'missing', size: null, capabilities: {}, recommended: false, tier: 'supported', reasons: ['Needed for good search'], approxRamGb: null },
  ],
  missingModels: ['nomic-embed-text'],
  installedNames: ['llama3.1:8b', 'qwen2.5-coder:7b'],
  presets: [],
  activeExperience: 'medium',
  roles: {},
}

describe('ModelsSettings', () => {
  it('shows the discovered library with install status and recommendations', () => {
    render(
      <ModelsSettings discovery={DISCOVERY} installing={{}} onInstall={vi.fn()} onRefresh={vi.fn()} loading={false} />
    )
    expect(screen.getByText('Llama 3.1 8B')).toBeTruthy()
    expect(screen.getByText('Qwen 2.5 Coder 7B')).toBeTruthy()
    expect(screen.getByText('Nomic Embed Text')).toBeTruthy()
    expect(screen.getByText('Tested with Cozmo')).toBeTruthy()
    expect(screen.getAllByText('installed')).toHaveLength(2)
  })

  it('shows an Install button only for non-installed models', () => {
    render(
      <ModelsSettings discovery={DISCOVERY} installing={{}} onInstall={vi.fn()} onRefresh={vi.fn()} loading={false} />
    )
    const buttons = screen.getAllByRole('button', { name: /install/i })
    expect(buttons).toHaveLength(1)
  })

  it('calls onInstall when the user installs a missing model', () => {
    const onInstall = vi.fn().mockResolvedValue(true)
    render(
      <ModelsSettings discovery={DISCOVERY} installing={{}} onInstall={onInstall} onRefresh={vi.fn()} loading={false} />
    )
    fireEvent.click(screen.getAllByRole('button', { name: /install/i })[0])
    expect(onInstall).toHaveBeenCalledWith('nomic-embed-text')
  })

  it('filters the list by query', () => {
    render(
      <ModelsSettings discovery={DISCOVERY} installing={{}} onInstall={vi.fn()} onRefresh={vi.fn()} loading={false} />
    )
    fireEvent.change(screen.getByRole('textbox', { name: '' }), { target: { value: 'qwen' } })
    expect(screen.queryByText('Llama 3.1 8B')).toBeNull()
    expect(screen.getByText('Qwen 2.5 Coder 7B')).toBeTruthy()
  })
})