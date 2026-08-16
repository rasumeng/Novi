import { describe, it, expect, vi } from 'vitest'
import { render, screen, fireEvent, within } from '@testing-library/react'
import { ModelsSettings } from './ModelsSettings'
import type { DiscoveryPayload, DiscoveredModelEntry, WorkloadRecommendation } from './api'

function installed(name: string, displayName: string, extra: Record<string, unknown> = {}): DiscoveredModelEntry {
  return {
    name,
    displayName,
    status: 'installed',
    size: null,
    capabilities: {},
    recommended: true,
    tier: 'supported',
    qualification: 'trusted',
    reasons: [],
    caveats: [],
    approxRamGb: 5,
    eligibility: { hardwareFit: 'fits', hardwareConfidence: 'high' },
    ...extra,
  }
}

function rec(workload: string, model: string, extra: Partial<WorkloadRecommendation> = {}): WorkloadRecommendation {
  return {
    workload,
    model,
    capability: '',
    qualification: 'supported',
    hardwareConfidence: 'high',
    reasons: ['Tested with Cozmo'],
    caveats: [],
    capabilities: [],
    visionCapable: false,
    ...extra,
  }
}

const RECOMMENDED = {
  workloads: {
    general: rec('general', 'llama3.1:8b'),
    research: rec('research', 'llama3.1:8b'),
    code: rec('code', 'qwen2.5-coder:7b'),
  },
  provisional: false,
}

const DISCOVERY: DiscoveryPayload = {
  hardware: { ramGb: 16 },
  models: [
    installed('llama3.1:8b', 'Llama 3.1 8B'),
    installed('qwen2.5-coder:7b', 'Qwen 2.5 Coder 7B'),
    { name: 'nomic-embed-text', displayName: 'Nomic Embed Text', status: 'missing', size: null, capabilities: { embeddings: true }, recommended: false, tier: 'supported', qualification: 'supported', reasons: ['Needed for good search'], caveats: [], approxRamGb: null, eligibility: { hardwareFit: 'unknown', hardwareConfidence: 'high' } },
  ],
  missingModels: ['nomic-embed-text'],
  installedNames: ['llama3.1:8b', 'qwen2.5-coder:7b'],
  dismissedRecommended: [],
  workloads: { general: '', research: '', code: '' },
  recommended: RECOMMENDED,
  vision_capable: false,
}

function renderPage(props?: {
  discovery?: DiscoveryPayload
  onSaveSelection?: (workloads: Record<string, string>) => Promise<{ ok: boolean; error?: string }>
  onApplyRecommended?: () => Promise<{ ok: boolean; error?: string }>
  onDismiss?: (name: string) => Promise<boolean>
}) {
  return render(
    <ModelsSettings
      discovery={props?.discovery ?? DISCOVERY}
      installing={{}}
      onInstall={vi.fn()}
      onDismiss={props?.onDismiss}
      onRefresh={vi.fn()}
      loading={false}
      onSaveSelection={props?.onSaveSelection ?? vi.fn().mockResolvedValue({ ok: true })}
      onApplyRecommended={props?.onApplyRecommended ?? vi.fn().mockResolvedValue({ ok: true })}
    />
  )
}

function selectionSection() {
  return within(screen.getByText('Current selection').closest('section')!)
}

describe('ModelsSettings — workload surface', () => {
  it('renders the full IA: recommendations, current selection, and model library', () => {
    renderPage()
    expect(screen.getByText('Recommended models')).toBeTruthy()
    expect(screen.getByText('Current selection')).toBeTruthy()
    expect(screen.getByText('Model library')).toBeTruthy()
  })

  it('shows the three workloads in Current selection only', () => {
    renderPage()
    const section = selectionSection()
    for (const w of ['General', 'Research', 'Code']) {
      expect(section.getByText(w)).toBeTruthy()
    }
    // Capability names and internal roles must NOT be workload selectors.
    expect(section.queryByText('Chat')).toBeNull()
    expect(section.queryByText('Vision')).toBeNull()
    expect(section.queryByText('Classifier')).toBeNull()
    expect(section.queryByText('Router')).toBeNull()
    expect(section.queryByText('Planner')).toBeNull()
  })

  it('has exactly three workload selectors', () => {
    renderPage()
    expect(selectionSection().getAllByRole('combobox')).toHaveLength(3)
  })

  it('does not offer embeddings as a workload choice', () => {
    renderPage()
    const selects = selectionSection().getAllByRole('combobox')
    const labels = selects.flatMap((s) => within(s).getAllByRole('option').map((o) => o.textContent))
    expect(labels).toContain('Llama 3.1 8B')
    expect(labels).toContain('Qwen 2.5 Coder 7B')
    expect(labels).not.toContain('Nomic Embed Text')
  })

  it('persists the full workloads map verbatim when one workload changes', () => {
    const onSaveSelection = vi.fn().mockResolvedValue({ ok: true })
    renderPage({ onSaveSelection })
    const selects = selectionSection().getAllByRole('combobox')
    fireEvent.change(selects[0], { target: { value: 'qwen2.5-coder:7b' } })
    expect(onSaveSelection).toHaveBeenCalledTimes(1)
    expect(onSaveSelection).toHaveBeenCalledWith({
      general: 'qwen2.5-coder:7b',
      research: '',
      code: '',
    })
  })

  it('warns when a selected model is not installed', () => {
    renderPage({
      discovery: {
        ...DISCOVERY,
        workloads: { general: 'gone:model', research: '', code: '' },
      },
    })
    expect(screen.getByText(/"gone:model" is not installed — Cozmo cannot use it until it is\./)).toBeTruthy()
  })

  it('shows a Vision-capable badge when the selected general model supports vision', () => {
    renderPage({
      discovery: {
        ...DISCOVERY,
        workloads: { general: 'qwen2.5vl:7b', research: '', code: '' },
        vision_capable: true,
        installedNames: ['qwen2.5vl:7b', 'llama3.1:8b', 'qwen2.5-coder:7b'],
        models: [
          installed('qwen2.5vl:7b', 'Qwen 2.5 VL 7B', { capabilities: { chat: true, vision: true } }),
          installed('llama3.1:8b', 'Llama 3.1 8B'),
          installed('qwen2.5-coder:7b', 'Qwen 2.5 Coder 7B'),
        ],
      },
    })
    const section = selectionSection()
    expect(section.getByText('Vision-capable')).toBeTruthy()
  })

  it('no Vision-capable badge when general selection cannot see', () => {
    renderPage()
    expect(selectionSection().queryByText('Vision-capable')).toBeNull()
  })

  it('lists advisory recommendations with installed status', () => {
    renderPage()
    const section = within(screen.getByLabelText('Recommended models'))
    expect(section.getByText('Recommended models')).toBeTruthy()
    expect(section.getByText('General')).toBeTruthy()
    expect(section.getByText('Research')).toBeTruthy()
    expect(section.getByText('Code')).toBeTruthy()
    expect(section.getAllByText('llama3.1:8b').length).toBeGreaterThanOrEqual(2)
    expect(section.getByText('qwen2.5-coder:7b')).toBeTruthy()
  })

  it('Use Recommended applies the advisory recommendations explicitly', () => {
    const onApplyRecommended = vi.fn().mockResolvedValue({ ok: true })
    renderPage({ onApplyRecommended })
    fireEvent.click(screen.getByRole('button', { name: /use recommended/i }))
    expect(onApplyRecommended).toHaveBeenCalledTimes(1)
  })

  it('Use Recommended is hidden when no recommendations exist', () => {
    renderPage({ discovery: { ...DISCOVERY, recommended: { workloads: {}, provisional: true } } })
    expect(screen.queryByRole('button', { name: /use recommended/i })).toBeNull()
    expect(screen.queryByLabelText('Recommended models')).toBeNull()
  })

  it('keeps the discovered library with install status', () => {
    renderPage()
    const section = within(screen.getByText('Model library').closest('section')!)
    expect(section.getByText('Llama 3.1 8B')).toBeTruthy()
    expect(section.getByText('Qwen 2.5 Coder 7B')).toBeTruthy()
    expect(section.getByText('Nomic Embed Text')).toBeTruthy()
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
      <ModelsSettings discovery={DISCOVERY} installing={{}} onInstall={onInstall} onRefresh={vi.fn()} loading={false} onSaveSelection={vi.fn().mockResolvedValue({ ok: true })} onApplyRecommended={vi.fn().mockResolvedValue({ ok: true })} />
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
})

describe('ModelsSettings — recommended model consent', () => {
  const RECO = (dismissed: string[] = []): DiscoveryPayload => ({
    hardware: { ramGb: 32 },
    models: [
      installed('llama3.1:8b', 'Llama 3.1 8B'),
      { name: 'qwen2.5vl:7b', displayName: 'Qwen 2.5 VL 7B', status: 'available', size: null, capabilities: { chat: true, vision: true }, recommended: true, tier: 'supported', qualification: 'trusted', reasons: ['Qualified: trusted', 'Best for your hardware'], caveats: [], approxRamGb: 8 },
      { name: 'nomic-embed-text', displayName: 'Nomic Embed Text', status: 'available', size: null, capabilities: { embeddings: true }, recommended: false, tier: 'supported', qualification: 'supported', reasons: ['Works with Memory'], caveats: [], approxRamGb: 1 },
    ],
    missingModels: [],
    installedNames: ['llama3.1:8b'],
    dismissedRecommended: dismissed,
    workloads: { general: '', research: '', code: '' },
    recommended: {
      workloads: {
        general: rec('general', 'qwen2.5vl:7b'),
        research: rec('research', 'llama3.1:8b'),
        code: rec('code', 'llama3.1:8b'),
      },
      provisional: false,
    },
    vision_capable: false,
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
    render(<ModelsSettings discovery={RECO()} installing={{}} onInstall={onInstall} onDismiss={vi.fn().mockResolvedValue(true)} onRefresh={vi.fn()} loading={false} onSaveSelection={vi.fn().mockResolvedValue({ ok: true })} onApplyRecommended={vi.fn().mockResolvedValue({ ok: true })} />)
    expect(onInstall).not.toHaveBeenCalled()
    fireEvent.click(screen.getByRole('button', { name: /install & use/i }))
    expect(onInstall).toHaveBeenCalledTimes(1)
    expect(onInstall).toHaveBeenCalledWith('qwen2.5vl:7b')
  })

  it('Not now dismisses the recommendation without installing', () => {
    const onDismiss = vi.fn().mockResolvedValue(true)
    const onInstall = vi.fn()
    render(<ModelsSettings discovery={RECO()} installing={{}} onInstall={onInstall} onDismiss={onDismiss} onRefresh={vi.fn()} loading={false} onSaveSelection={vi.fn().mockResolvedValue({ ok: true })} onApplyRecommended={vi.fn().mockResolvedValue({ ok: true })} />)
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
    expect(section.getAllByRole('button', { name: /install & use/i })).toHaveLength(1)
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
        onSaveSelection={vi.fn().mockResolvedValue({ ok: true })}
        onApplyRecommended={vi.fn().mockResolvedValue({ ok: true })}
      />
    )
    const setup = within(screen.getByLabelText('Recommended model setup'))
    expect((setup.getByRole('button', { name: /installing/i }) as HTMLButtonElement).disabled).toBe(true)
    expect(setup.queryByRole('button', { name: /not now/i })).toBeNull()
  })
})