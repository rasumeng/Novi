import { describe, it, expect, vi } from 'vitest'
import { render, screen, fireEvent, within } from '@testing-library/react'
import { ModelsSettings } from './ModelsSettings'
import type { DiscoveryPayload } from './api'

const DISCOVERY: DiscoveryPayload = {
  hardware: { ramGb: 16 },
  models: [
    { name: 'llama3.1:8b', displayName: 'Llama 3.1 8B', status: 'installed', size: null, capabilities: {}, recommended: true, tier: 'supported', qualification: 'trusted', reasons: ['Tested with Cozmo'], caveats: [], approxRamGb: 5, eligibility: { hardwareFit: 'fits', hardwareConfidence: 'high', eligibleAutomatic: true, eligibleCustom: true } },
    { name: 'qwen2.5-coder:7b', displayName: 'Qwen 2.5 Coder 7B', status: 'installed', size: null, capabilities: {}, recommended: true, tier: 'supported', qualification: 'supported', reasons: [], caveats: [], approxRamGb: 6, eligibility: { hardwareFit: 'fits', hardwareConfidence: 'high', eligibleAutomatic: true, eligibleCustom: true } },
    { name: 'nomic-embed-text', displayName: 'Nomic Embed Text', status: 'missing', size: null, capabilities: {}, recommended: false, tier: 'supported', qualification: 'supported', reasons: ['Needed for good search'], caveats: [], approxRamGb: null, eligibility: { hardwareFit: 'unknown', hardwareConfidence: 'high', eligibleAutomatic: false, eligibleCustom: false } },
  ],
  missingModels: ['nomic-embed-text'],
  installedNames: ['llama3.1:8b', 'qwen2.5-coder:7b'],
  dismissedRecommended: [],
  presets: [],
  activeExperience: 'medium',
  roles: { chat: 'llama3.1:8b', planner: 'llama3.1:8b', coder: 'qwen2.5-coder:7b', vision: '' },
}

type StateFn = (mode: 'automatic' | 'custom', assign?: Record<string, string>) => Promise<{ ok: boolean; error?: string }>

function renderPage(props?: {
  mode?: string
  source?: string
  customAssign?: Record<string, string>
  onSetModelsState?: StateFn
  onDismiss?: (name: string) => Promise<boolean>
  discovery?: DiscoveryPayload
}) {
  return render(
    <ModelsSettings
      discovery={props?.discovery ?? DISCOVERY}
      installing={{}}
      onInstall={vi.fn()}
      onDismiss={props?.onDismiss}
      onRefresh={vi.fn()}
      loading={false}
      mode={props?.mode ?? 'automatic'}
      source={props?.source ?? 'automatic'}
      customAssign={props?.customAssign}
      onSetModelsState={props?.onSetModelsState}
    />
  )
}

function customSection() {
  return within(screen.getByText('Custom configuration').closest('section')!)
}

function assignmentSection() {
  return within(screen.getByText('Current assignments').closest('section')!)
}

describe('ModelsSettings — M3.1 skeleton', () => {
  it('renders the full IA: configuration mode, current assignments, custom config, and model library', () => {
    renderPage()
    expect(screen.getAllByText('Automatic').length).toBeGreaterThan(0)
    expect(screen.getAllByText('Custom').length).toBeGreaterThan(0)
    expect(screen.getByText('Current assignments')).toBeTruthy()
    expect(screen.getByText('Custom configuration')).toBeTruthy()
    expect(screen.getByText('Model library')).toBeTruthy()
  })

  it('shows the four user-facing capabilities in Current Assignments only', () => {
    renderPage()
    const section = assignmentSection()
    for (const cap of ['Chat', 'Reasoning', 'Coding', 'Vision']) {
      expect(section.getByText(cap)).toBeTruthy()
    }
    // Internal runtime roles must NOT be capability selectors/assignments.
    expect(section.queryByText('Classifier')).toBeNull()
    expect(section.queryByText('Router')).toBeNull()
    expect(section.queryByText('Orchestrator')).toBeNull()
    expect(section.queryByText('Planner')).toBeNull()
  })

  it('does not expose Embeddings as a user-facing capability', () => {
    renderPage()
    const inAssignments = assignmentSection()
    const inCustom = customSection()
    expect(inAssignments.queryByText(/embed/i)).toBeNull()
    expect(inCustom.queryByText(/embedding model/i)).toBeNull()
    expect(inAssignments.queryByText('Nomic Embed Text')).toBeNull()
    // The four custom selects cover exactly the four capabilities.
    expect(inCustom.getAllByRole('combobox')).toHaveLength(4)
  })

  it('keeps the discovered library with install status and recommendations', () => {
    renderPage()
    const section = within(screen.getByText('Model library').closest('section')!)
    expect(section.getByText('Llama 3.1 8B')).toBeTruthy()
    expect(section.getByText('Qwen 2.5 Coder 7B')).toBeTruthy()
    expect(section.getByText('Nomic Embed Text')).toBeTruthy()
    expect(section.getByText('Tested with Cozmo')).toBeTruthy()
    expect(section.getAllByText('installed')).toHaveLength(2)
  })

  it('shows an Install button only for non-installed models', () => {
    renderPage()
    const buttons = screen.getAllByRole('button', { name: /install/i })
    expect(buttons).toHaveLength(1)
  })

  it('calls onInstall when the user installs a missing model', () => {
    const onInstall = vi.fn().mockResolvedValue(true)
    render(
      <ModelsSettings discovery={DISCOVERY} installing={{}} onInstall={onInstall} onRefresh={vi.fn()} loading={false} mode="automatic" />
    )
    fireEvent.click(screen.getAllByRole('button', { name: /install/i })[0])
    expect(onInstall).toHaveBeenCalledWith('nomic-embed-text')
  })

  it('filters the model library list by query', () => {
    renderPage()
    const input = document.querySelector('input[placeholder="Filter by name…"]') as HTMLInputElement
    fireEvent.change(input, { target: { value: 'qwen' } })
    const section = within(screen.getByText('Model library').closest('section')!)
    expect(section.queryByText('Llama 3.1 8B')).toBeNull()
    expect(section.getByText('Qwen 2.5 Coder 7B')).toBeTruthy()
  })

  it('shows an automatic explanation when in Automatic mode', () => {
    renderPage()
    expect(screen.getByText('How models were chosen')).toBeTruthy()
  })
})

describe('ModelsSettings — M3.2 state machine', () => {
  it('Automatic mode renders capability selectors read-only and sources Automatic', () => {
    renderPage()
    const selects = customSection().getAllByRole('combobox')
    expect(selects).toHaveLength(4)
    expect(selects.every((s) => (s as HTMLSelectElement).disabled)).toBe(true)
    // Current assignments all show Automatic provenance (no explicit intent).
    const badges = assignmentSection().getAllByText('Automatic')
    expect(badges.length).toBeGreaterThanOrEqual(4)
    expect(assignmentSection().queryByText('Custom')).toBeNull()
  })

  it('Switch to Custom seeds the four capabilities from the effective assignments', () => {
    const onSetModelsState = vi.fn().mockResolvedValue({ ok: true })
    renderPage({ onSetModelsState })
    fireEvent.click(screen.getByRole('button', { name: /switch to custom/i }))
    expect(onSetModelsState).toHaveBeenCalledTimes(1)
    expect(onSetModelsState).toHaveBeenCalledWith('custom', {
      chat: 'llama3.1:8b',
      reasoning: 'llama3.1:8b',
      coding: 'qwen2.5-coder:7b',
      vision: '',
    })
    // No embeddings capability is seeded.
    expect(Object.keys(onSetModelsState.mock.calls[0][1])).not.toContain('embedding')
  })

  it('changing a capability selector persists only that capability and stays Custom', () => {
    const onSetModelsState = vi.fn().mockResolvedValue({ ok: true })
    renderPage({ mode: 'custom', source: 'custom', customAssign: { chat: 'llama3.1:8b' }, onSetModelsState })
    const selects = customSection().getAllByRole('combobox')
    const chat = selects[0]
    fireEvent.change(chat, { target: { value: 'qwen2.5-coder:7b' } })
    expect(onSetModelsState).toHaveBeenCalledWith('custom', { chat: 'qwen2.5-coder:7b' })
  })

  it('changing one capability does not affect the other selectors', () => {
    const onSetModelsState = vi.fn().mockResolvedValue({ ok: true })
    const assign = { chat: 'llama3.1:8b', reasoning: 'llama3.1:8b', coding: 'qwen2.5-coder:7b', vision: 'llama3.1:8b' }
    renderPage({ mode: 'custom', source: 'custom', customAssign: assign, onSetModelsState })
    const selects = customSection().getAllByRole('combobox')
    fireEvent.change(selects[2], { target: { value: 'qwen2.5-coder:7b' } })
    expect(onSetModelsState).toHaveBeenCalledWith('custom', { coding: 'qwen2.5-coder:7b' })
    // Unrelated capabilities are untouched in the persisted payload.
    expect(onSetModelsState.mock.calls[0][1]).toEqual({ coding: 'qwen2.5-coder:7b' })
  })

  it('unset capability inherits Automatic and is not written as user intent', () => {
    const onSetModelsState = vi.fn().mockResolvedValue({ ok: true })
    renderPage({ mode: 'custom', source: 'custom', customAssign: { chat: 'llama3.1:8b' }, onSetModelsState })
    const section = customSection()
    // All four custom selects exist; the unset capability shows the Inherit placeholder.
    const visionLabel = section.getByText('Vision')
    const visionRow = visionLabel.closest('div')!
    expect(within(visionRow).getByText(/Inherits Automatic/)).toBeTruthy()
    // Current assignments badge for the unset capability is Automatic.
    expect(assignmentSection().getAllByText('Automatic').length).toBeGreaterThan(0)
    expect(section.getAllByRole('combobox').length).toBe(4)
  })

  it('Custom provenance shows for explicitly assigned capabilities only', () => {
    renderPage({ mode: 'custom', source: 'custom', customAssign: { chat: 'llama3.1:8b' } })
    // Chat is explicitly custom -> Custom badge present somewhere.
    const customBadges = assignmentSection().getAllByText('Custom')
    expect(customBadges.length).toBeGreaterThan(0)
    // At least one capability (vision) remains Automatic-derived.
    expect(assignmentSection().getAllByText('Automatic').length).toBeGreaterThan(0)
  })

  it('missing custom model is marked unavailable while intent is preserved', () => {
    renderPage({
      mode: 'custom',
      source: 'custom',
      customAssign: { chat: 'gone:model', coding: 'qwen2.5-coder:7b' },
    })
    // Custom row warns the chosen model is unavailable.
    expect(screen.getByText(/"gone:model" is not installed — Cozmo is temporarily using a fallback\./)).toBeTruthy()
    // Current assignment shows the unavailable warning.
    expect(screen.getByText(/your choice \(gone:model\) is unavailable/)).toBeTruthy()
    // Mode remains Custom.
    expect(screen.getAllByText('Custom').length).toBeGreaterThan(0)
  })

  it('Use Automatic returns to automatic resolution', () => {
    const onSetModelsState = vi.fn().mockResolvedValue({ ok: true })
    renderPage({ mode: 'custom', source: 'custom', customAssign: { chat: 'llama3.1:8b' }, onSetModelsState })
    fireEvent.click(screen.getByRole('button', { name: /use automatic/i }))
    expect(onSetModelsState).toHaveBeenCalledWith('automatic', undefined)
  })

  it('Custom selectors list actual discovered installed models only', () => {
    renderPage({ mode: 'custom', source: 'custom', customAssign: { chat: 'llama3.1:8b' } })
    const section = customSection()
    const select = section.getAllByRole('combobox')[0]
    const options = within(select).getAllByRole('option')
    const labels = options.map((o) => o.textContent)
    expect(labels).toContain('Llama 3.1 8B')
    expect(labels).toContain('Qwen 2.5 Coder 7B')
    // The missing embedding model is NOT offered as a capability choice.
    expect(labels).not.toContain('Nomic Embed Text')
  })
})

describe('ModelsSettings — M3.4 consent', () => {
  const RECO = (dismissed: string[] = []): DiscoveryPayload => ({
    hardware: { ramGb: 32 },
    models: [
      { name: 'llama3.1:8b', displayName: 'Llama 3.1 8B', status: 'installed', size: null, capabilities: {}, recommended: true, tier: 'supported', qualification: 'supported', reasons: ['Tested with Cozmo'], caveats: [], approxRamGb: 5, eligibility: { hardwareFit: 'fits', hardwareConfidence: 'high', eligibleAutomatic: true, eligibleCustom: true } },
      { name: 'qwen2.5vl:7b', displayName: 'Qwen 2.5 VL 7B', status: 'available', size: null, capabilities: { chat: true, vision: true }, recommended: true, tier: 'supported', qualification: 'trusted', reasons: ['Qualified: trusted', 'Best for your hardware'], caveats: [], approxRamGb: 8 },
      { name: 'nomic-embed-text', displayName: 'Nomic Embed Text', status: 'available', size: null, capabilities: { embeddings: true }, recommended: false, tier: 'supported', qualification: 'supported', reasons: ['Works with Memory'], caveats: [], approxRamGb: 1 },
    ],
    missingModels: [],
    installedNames: ['llama3.1:8b'],
    dismissedRecommended: dismissed,
    presets: [],
    activeExperience: 'medium',
    roles: { chat: 'llama3.1:8b', planner: 'llama3.1:8b', coder: 'llama3.1:8b', vision: 'llama3.1:8b' },
  })

  it('shows recommended-but-missing models with explicit consent actions', () => {
    const onDismiss = vi.fn().mockResolvedValue(true)
    renderPage({ discovery: RECO(), onDismiss })
    const setup = within(screen.getByLabelText('Recommended model setup'))
    expect(setup.getByText('Recommended model unavailable')).toBeTruthy()
    expect(setup.getByText('Qwen 2.5 VL 7B')).toBeTruthy()
    expect(setup.getByRole('button', { name: /install & use/i })).toBeTruthy()
    expect(setup.getByRole('button', { name: /not now/i })).toBeTruthy()
  })

  it('does not install anything before an explicit consent click', () => {
    const onInstall = vi.fn().mockResolvedValue(true)
    render(<ModelsSettings discovery={RECO()} installing={{}} onInstall={onInstall} onDismiss={vi.fn().mockResolvedValue(true)} onRefresh={vi.fn()} loading={false} mode="automatic" />)
    expect(onInstall).not.toHaveBeenCalled()
    fireEvent.click(screen.getByRole('button', { name: /install & use/i }))
    expect(onInstall).toHaveBeenCalledTimes(1)
    expect(onInstall).toHaveBeenCalledWith('qwen2.5vl:7b')
  })

  it('Not now dismisses the recommendation without installing', () => {
    const onDismiss = vi.fn().mockResolvedValue(true)
    const onInstall = vi.fn()
    render(<ModelsSettings discovery={RECO()} installing={{}} onInstall={onInstall} onDismiss={onDismiss} onRefresh={vi.fn()} loading={false} mode="automatic" />)
    fireEvent.click(screen.getByRole('button', { name: /not now/i }))
    expect(onDismiss).toHaveBeenCalledWith('qwen2.5vl:7b')
    expect(onInstall).not.toHaveBeenCalled()
  })

  it('dismissed models disappear from the setup card', () => {
    renderPage({ discovery: RECO(['qwen2.5vl:7b']) })
    expect(screen.queryByText('Recommended model unavailable')).toBeNull()
  })

  it('embeddings never appear in the setup/install UI', () => {
    renderPage({ discovery: RECO() })
    const section = within(screen.getByLabelText('Recommended model setup'))
    expect(section.queryByText(/embed/i)).toBeNull()
    expect(section.queryByText('Nomic Embed Text')).toBeNull()
    // Only the chat/vision recommended model is offered for install.
    expect(section.getAllByRole('button', { name: /install & use/i })).toHaveLength(1)
  })

  it('consent card is hidden entirely in Custom mode', () => {
    renderPage({ mode: 'custom', source: 'custom', customAssign: { chat: 'llama3.1:8b' }, discovery: RECO() })
    expect(screen.queryByText('Recommended model unavailable')).toBeNull()
  })

  it('install is disabled and Not now hidden while an install is in flight', () => {
    render(
      <ModelsSettings
        discovery={RECO()}
        installing={{ 'qwen2.5vl:7b': { phase: 'installing', pct: 40 } }}
        onInstall={vi.fn().mockResolvedValue(true)}
        onDismiss={vi.fn().mockResolvedValue(true)}
        onRefresh={vi.fn()}
        loading={false}
        mode="automatic"
      />
    )
    const setup = within(screen.getByLabelText('Recommended model setup'))
    expect((setup.getByRole('button', { name: /installing/i }) as HTMLButtonElement).disabled).toBe(true)
    expect(setup.queryByRole('button', { name: /not now/i })).toBeNull()
  })
})
