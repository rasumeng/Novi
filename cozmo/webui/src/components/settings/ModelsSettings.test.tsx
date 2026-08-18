import { describe, it, expect, vi } from 'vitest'
import { render, screen, fireEvent, within, waitFor } from '@testing-library/react'
import { readFileSync } from 'node:fs'
import { join } from 'node:path'
import { ModelsSettings } from './ModelsSettings'
import type { CapabilityEvidence, DiscoveryPayload, DiscoveredModelEntry, RecommendationExplanation, SchemaResponse, WorkloadRecommendation } from './api'

function capabilityEvidence(capability: string, supported: boolean | null, source: string): CapabilityEvidence {
  return { capability, supported, source, confidence: null, note: '' }
}

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
  hardware: { ramGb: 16, gpu: { name: 'NVIDIA GeForce RTX 4060', vramTotalGb: 8, vendor: 'nvidia' }, confidence: 'high' },
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

// Selection equals every recommendation — the "Using recommended" steady state.
const USING_RECOMMENDED: DiscoveryPayload = {
  ...DISCOVERY,
  workloads: { general: 'llama3.1:8b', research: 'llama3.1:8b', code: 'qwen2.5-coder:7b' },
}

// General diverges from its recommendation; research matches; code unset.
const DIFFERENT: DiscoveryPayload = {
  ...DISCOVERY,
  workloads: { general: 'qwen2.5-coder:7b', research: 'llama3.1:8b', code: '' },
}

// Backend configuration schema: the single source for workload labels/descs.
const SCHEMA: SchemaResponse = {
  settings: [
    { id: 'llm.workloads.general.model', label: 'General', description: 'Model used for general interaction.', category: 'models', owner: 'runtime', type: 'model', default: '', options: [], restart_required: false, depends: [], visibility: 'user' },
    { id: 'llm.workloads.research.model', label: 'Deep Research', description: 'Model used for deep research and multi-step planning tasks.', category: 'models', owner: 'runtime', type: 'model', default: '', options: [], restart_required: false, depends: [], visibility: 'user' },
    { id: 'llm.workloads.code.model', label: 'Code', description: 'Model used for code generation and editing.', category: 'models', owner: 'runtime', type: 'model', default: '', options: [], restart_required: false, depends: [], visibility: 'user' },
  ],
  groups: [],
}

function renderPage(props?: {
  discovery?: DiscoveryPayload
  onSaveSelection?: (workloads: Record<string, string>) => Promise<{ ok: boolean; error?: string }>
  onApplyRecommended?: (workloads?: string[]) => Promise<{ ok: boolean; error?: string }>
  onDismiss?: (name: string) => Promise<boolean>
  onDelete?: (name: string) => Promise<boolean>
}) {
  return render(
    <ModelsSettings
      discovery={props?.discovery ?? DISCOVERY}
      schema={SCHEMA}
      installing={{}}
      onInstall={vi.fn()}
      onDelete={props?.onDelete ?? vi.fn().mockResolvedValue(true)}
      onDismiss={props?.onDismiss}
      onRefresh={vi.fn()}
      loading={false}
      onSaveSelection={props?.onSaveSelection ?? vi.fn().mockResolvedValue({ ok: true })}
      onApplyRecommended={props?.onApplyRecommended ?? vi.fn().mockResolvedValue({ ok: true })}
    />
  )
}

function selectionSection() {
  return within(screen.getByText('Model selection').closest('section')!)
}

function librarySection() {
  return within(screen.getByText('Model library').closest('section')!)
}

describe('ModelsSettings — workload surface', () => {
  it('renders the full IA: recommendations, current selection, and model library', () => {
    renderPage()
    expect(screen.getByText('Recommended models')).toBeTruthy()
    expect(screen.getByText('Model selection')).toBeTruthy()
    expect(screen.getByText('Model library')).toBeTruthy()
  })

  it('shows the three workloads in Current selection only', () => {
    renderPage()
    const section = selectionSection()
    for (const w of ['General', 'Deep Research', 'Code']) {
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
    expect(section.getByText('Deep Research')).toBeTruthy()
    expect(section.getByText('Code')).toBeTruthy()
    expect(section.getAllByText('llama3.1:8b').length).toBeGreaterThanOrEqual(2)
    expect(section.getByText('qwen2.5-coder:7b')).toBeTruthy()
  })

  it('Use Recommended applies the advisory recommendations explicitly', () => {
    const onApplyRecommended = vi.fn().mockResolvedValue({ ok: true })
    renderPage({ onApplyRecommended })
    const section = within(screen.getByLabelText('Recommended models'))
    fireEvent.click(section.getAllByRole('button', { name: /use recommended/i })[0])
    expect(onApplyRecommended).toHaveBeenCalledTimes(1)
    expect(onApplyRecommended.mock.calls[0]).toEqual([])
  })

  it('per-workload Use Recommended applies only that workload', () => {
    const onApplyRecommended = vi.fn().mockResolvedValue({ ok: true })
    renderPage({ onApplyRecommended })
    fireEvent.click(selectionSection().getByTitle('Use the recommended model for Code'))
    expect(onApplyRecommended).toHaveBeenCalledTimes(1)
    expect(onApplyRecommended).toHaveBeenCalledWith(['code'])
  })

  it('shows Applying… and disables buttons while an apply is in flight', () => {
    let resolve!: (v: { ok: boolean }) => void
    const onApplyRecommended = vi.fn().mockReturnValue(new Promise((r) => { resolve = r }))
    renderPage({ onApplyRecommended })
    const section = selectionSection()
    fireEvent.click(section.getByTitle('Use the recommended model for Code'))
    const btn = section.getByTitle('Use the recommended model for Code') as HTMLButtonElement
    expect(btn.disabled).toBe(true)
    expect(section.getAllByText('Applying…').length).toBeGreaterThanOrEqual(1)
    resolve({ ok: true })
  })

  it('Use Recommended is hidden when no recommendations exist', () => {
    renderPage({ discovery: { ...DISCOVERY, recommended: { workloads: {}, provisional: true } } })
    expect(screen.queryByRole('button', { name: /use recommended/i })).toBeNull()
    expect(screen.queryByLabelText('Recommended models')).toBeNull()
    const section = selectionSection()
    expect(section.getAllByText('No recommendation available')).toHaveLength(3)
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
      <ModelsSettings discovery={DISCOVERY} schema={SCHEMA} installing={{}} onInstall={onInstall} onDelete={vi.fn().mockResolvedValue(true)} onRefresh={vi.fn()} loading={false} onSaveSelection={vi.fn().mockResolvedValue({ ok: true })} onApplyRecommended={vi.fn().mockResolvedValue({ ok: true })} />
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
    hardware: { ramGb: 32, gpu: { name: 'NVIDIA GeForce RTX 4060', vramTotalGb: 8, vendor: 'nvidia' }, confidence: 'high' },
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
    render(<ModelsSettings discovery={RECO()} schema={SCHEMA} installing={{}} onInstall={onInstall} onDelete={vi.fn().mockResolvedValue(true)} onDismiss={vi.fn().mockResolvedValue(true)} onRefresh={vi.fn()} loading={false} onSaveSelection={vi.fn().mockResolvedValue({ ok: true })} onApplyRecommended={vi.fn().mockResolvedValue({ ok: true })} />)
    expect(onInstall).not.toHaveBeenCalled()
    fireEvent.click(screen.getByRole('button', { name: /install & use/i }))
    expect(onInstall).toHaveBeenCalledTimes(1)
    expect(onInstall).toHaveBeenCalledWith('qwen2.5vl:7b')
  })

  it('Not now dismisses the recommendation without installing', () => {
    const onDismiss = vi.fn().mockResolvedValue(true)
    const onInstall = vi.fn()
    render(<ModelsSettings discovery={RECO()} schema={SCHEMA} installing={{}} onInstall={onInstall} onDelete={vi.fn().mockResolvedValue(true)} onDismiss={onDismiss} onRefresh={vi.fn()} loading={false} onSaveSelection={vi.fn().mockResolvedValue({ ok: true })} onApplyRecommended={vi.fn().mockResolvedValue({ ok: true })} />)
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
        schema={SCHEMA}
        installing={{ 'qwen2.5vl:7b': { phase: 'installing', pct: 40 } }}
        onInstall={vi.fn().mockResolvedValue(true)}
        onDelete={vi.fn().mockResolvedValue(true)}
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

describe('ModelsSettings — derived recommendation indicators', () => {
  it('shows a Using recommended badge when the selection equals the recommendation', () => {
    renderPage({ discovery: USING_RECOMMENDED })
    const section = selectionSection()
    expect(section.getAllByText('Using recommended')).toHaveLength(3)
    expect(section.queryByText('Recommendation changed')).toBeNull()
  })

  it('shows Recommendation changed when a selection diverges from its recommendation', () => {
    renderPage({ discovery: DIFFERENT })
    const section = selectionSection()
    expect(section.getByText('Recommendation changed')).toBeTruthy()
    expect(section.getAllByText('Using recommended')).toHaveLength(1)
    expect(within(section.getByText('Recommendation changed').closest('div')!)
      .getByText('General')).toBeTruthy()
  })

  it('shows no indicator when nothing is selected', () => {
    renderPage({ discovery: DISCOVERY })
    const section = selectionSection()
    expect(section.queryByText('Using recommended')).toBeNull()
    expect(section.queryByText('Recommendation changed')).toBeNull()
  })

  it('never flags an empty selection as changed even with a recommendation', () => {
    renderPage({ discovery: DISCOVERY })
    const section = selectionSection()
    expect(section.getAllByText(/Recommended:/)).toHaveLength(3)
    expect(section.queryByText('Recommendation changed')).toBeNull()
  })

  it('shows No recommendation available when a workload lacks a recommendation', () => {
    renderPage({
      discovery: {
        ...DISCOVERY,
        workloads: { general: 'llama3.1:8b', research: 'llama3.1:8b', code: 'qwen2.5-coder:7b' },
        recommended: {
          ...DISCOVERY.recommended,
          workloads: { general: rec('general', 'llama3.1:8b'), research: rec('research', 'llama3.1:8b') },
        },
      },
    })
    const section = selectionSection()
    // code is selected but has no recommendation -> "No recommendation available",
    // and crucially no derived indicator (an un-recommended selection is not
    // "changed").
    expect(section.getByText('No recommendation available')).toBeTruthy()
    expect(section.getAllByText('Using recommended')).toHaveLength(2)
    expect(section.queryByText('Recommendation changed')).toBeNull()
  })

  it('shows the advisory Recommended: <model> line in each selection row', () => {
    renderPage()
    const section = selectionSection()
    expect(section.getAllByText(/Recommended:/)).toHaveLength(3)
    expect(section.getAllByText('llama3.1:8b').length).toBeGreaterThanOrEqual(2)
    expect(section.getByText('qwen2.5-coder:7b')).toBeTruthy()
  })

  it('flips to Using recommended after a refreshed discovery shows the apply landed', () => {
    const { rerender } = renderPage({ discovery: DIFFERENT })
    let section = selectionSection()
    expect(section.getByText('Recommendation changed')).toBeTruthy()
    rerender(
      <ModelsSettings
        discovery={USING_RECOMMENDED}
        schema={SCHEMA}
        installing={{}}
        onInstall={vi.fn()}
        onDelete={vi.fn().mockResolvedValue(true)}
        onRefresh={vi.fn()}
        loading={false}
        onSaveSelection={vi.fn().mockResolvedValue({ ok: true })}
        onApplyRecommended={vi.fn().mockResolvedValue({ ok: true })}
      />
    )
    section = selectionSection()
    expect(section.queryByText('Recommendation changed')).toBeNull()
    expect(section.getAllByText('Using recommended')).toHaveLength(3)
  })

  it('ModelsSettings never references the retired selection concept', () => {
    const src = readFileSync(join(process.cwd(), 'src/components/settings/ModelsSettings.tsx'), 'utf8')
    expect(/\b[Aa]utomatic\b/.test(src)).toBe(false)
  })
})

describe('ModelsSettings — Model library intelligence', () => {
  const available = (name: string, displayName: string, extra: Record<string, unknown> = {}): DiscoveredModelEntry => ({
    name,
    displayName,
    status: 'available',
    size: null,
    capabilities: {},
    recommended: true,
    tier: 'supported',
    qualification: 'supported',
    reasons: [],
    caveats: [],
    approxRamGb: 6,
    eligibility: { hardwareFit: 'fits', hardwareConfidence: 'high' },
    ...extra,
  })

  it('installed model with hardwareFit=fits renders the fit state', () => {
    renderPage({
      discovery: {
        ...DISCOVERY,
        models: [installed('llama3.1:8b', 'Llama 3.1 8B', {
          eligibility: { hardwareFit: 'fits', hardwareConfidence: 'high' },
        })],
      },
    })
    expect(librarySection().getByText('Fits detected hardware')).toBeTruthy()
  })

  it('hardwareFit=does_not_fit renders an incompatible-hardware state', () => {
    renderPage({
      discovery: {
        ...DISCOVERY,
        models: [installed('llama3.1:8b', 'Llama 3.1 8B', {
          eligibility: { hardwareFit: 'does_not_fit', hardwareConfidence: 'high' },
        })],
      },
    })
    expect(librarySection().getByText('Does not fit current hardware')).toBeTruthy()
  })

  it('does_not_fit available model has no usable Install action', () => {
    renderPage({
      discovery: {
        ...DISCOVERY,
        installedNames: [],
        models: [available('big-model:70b', 'Big Model 70B', {
          approxRamGb: 60,
          eligibility: { hardwareFit: 'does_not_fit', hardwareConfidence: 'high' },
        })],
      },
    })
    const section = librarySection()
    expect(section.queryByRole('button', { name: /install/i })).toBeNull()
    expect(section.getByText(/not recommended for the detected hardware/i)).toBeTruthy()
    // still visible in the library — not deleted, metadata viewable
    expect(section.getByText('Big Model 70B')).toBeTruthy()
  })

  it('hardwareFit=unknown renders the unknown state', () => {
    renderPage({
      discovery: {
        ...DISCOVERY,
        models: [installed('llama3.1:8b', 'Llama 3.1 8B', {
          eligibility: { hardwareFit: 'unknown', hardwareConfidence: 'unknown' },
        })],
      },
    })
    expect(librarySection().getByText('Hardware fit unknown')).toBeTruthy()
  })

  it('unknown hardware fit does not block Install', () => {
    renderPage({
      discovery: {
        ...DISCOVERY,
        installedNames: [],
        models: [available('mid-model:7b', 'Mid Model 7B', {
          eligibility: { hardwareFit: 'unknown', hardwareConfidence: 'medium' },
        })],
      },
    })
    const section = librarySection()
    expect(section.getByText('Hardware fit unknown')).toBeTruthy()
    expect(section.getByRole('button', { name: /install/i })).toBeTruthy()
  })

  it('runtime capability evidence renders runtime provenance', () => {
    renderPage({
      discovery: {
        ...DISCOVERY,
        models: [installed('llama3.1:8b', 'Llama 3.1 8B', {
          capabilityEvidence: [capabilityEvidence('coding', true, 'runtime')],
        })],
      },
    })
    const section = librarySection()
    expect(section.getByText('Coding')).toBeTruthy()
    expect(section.getByText(/Runtime evidence/)).toBeTruthy()
  })

  it('seed capability evidence renders curated provenance', () => {
    renderPage({
      discovery: {
        ...DISCOVERY,
        models: [installed('llama3.1:8b', 'Llama 3.1 8B', {
          capabilityEvidence: [capabilityEvidence('vision', true, 'seed')],
        })],
      },
    })
    expect(librarySection().getByText(/Curated evidence/)).toBeTruthy()
  })

  it('name-inference evidence is visibly weaker/advisory', () => {
    renderPage({
      discovery: {
        ...DISCOVERY,
        models: [installed('llama3.1:8b', 'Llama 3.1 8B', {
          capabilityEvidence: [capabilityEvidence('tools', true, 'name-inference')],
        })],
      },
    })
    const section = librarySection()
    expect(section.getByText(/Name-based hint/)).toBeTruthy()
    expect(section.getByText(/(weak)/)).toBeTruthy()
  })

  it('unknown capability renders "Unknown", never "unsupported"', () => {
    renderPage({
      discovery: {
        ...DISCOVERY,
        models: [installed('llama3.1:8b', 'Llama 3.1 8B', {
          eligibility: { hardwareFit: 'fits', hardwareConfidence: 'high' },
          capabilityEvidence: [capabilityEvidence('vision', null, 'runtime')],
        })],
      },
    })
    const section = librarySection()
    expect(section.getByText('Unknown')).toBeTruthy()
    expect(section.queryByText('not supported')).toBeNull()
    expect(section.queryByText('unsupported')).toBeNull()
  })

  it('renders qualification states distinctly', () => {
    const quals = ['trusted', 'supported', 'experimental', 'incompatible']
    renderPage({
      discovery: {
        ...DISCOVERY,
        models: quals.map((q, i) => installed(`m${i}:1b`, `Model ${i}`, {
          qualification: q,
          eligibility: { hardwareFit: 'fits', hardwareConfidence: 'high' },
        })),
      },
    })
    const section = librarySection()
    expect(section.getByText('Trusted')).toBeTruthy()
    expect(section.getByText('Supported')).toBeTruthy()
    expect(section.getByText('Experimental')).toBeTruthy()
    expect(section.getByText('Incompatible')).toBeTruthy()
  })

  it('stale=true renders a stale-inventory indicator', () => {
    renderPage({
      discovery: {
        ...DISCOVERY,
        models: [installed('llama3.1:8b', 'Llama 3.1 8B', { stale: true })],
      },
    })
    const section = librarySection()
    expect(section.getByText(/stale inventory/i)).toBeTruthy()
    expect(section.getByText(/cached model information/i)).toBeTruthy()
  })

  it('distinguishes installed from available models', () => {
    renderPage({
      discovery: {
        ...DISCOVERY,
        models: [
          installed('llama3.1:8b', 'Llama 3.1 8B'),
          available('qwen2.5vl:7b', 'Qwen 2.5 VL 7B'),
        ],
      },
    })
    const section = librarySection()
    expect(section.getAllByText('installed')).toHaveLength(1)
    expect(section.getAllByText('available')).toHaveLength(1)
    expect(section.getByText(/available to install/)).toBeTruthy()
  })

  it('keeps a missing configured model visible as missing', () => {
    renderPage({
      discovery: {
        ...DISCOVERY,
        workloads: { general: 'gone:model', research: '', code: '' },
        installedNames: [],
        models: [
          { name: 'gone:model', displayName: 'Gone Model', status: 'missing', size: null, capabilities: {}, recommended: false, tier: 'supported', qualification: 'supported', reasons: [], caveats: [], approxRamGb: null, eligibility: { hardwareFit: 'unknown', hardwareConfidence: 'unknown' } },
        ],
      },
    })
    const section = librarySection()
    expect(section.getByText('Gone Model')).toBeTruthy()
    expect(section.getByText('missing')).toBeTruthy()
  })

  it('missing configured model does not cause selection mutation', () => {
    const onSaveSelection = vi.fn()
    const onApplyRecommended = vi.fn().mockResolvedValue({ ok: true })
    renderPage({
      discovery: {
        ...DISCOVERY,
        workloads: { general: 'gone:model', research: '', code: '' },
      },
      onSaveSelection,
      onApplyRecommended,
    })
    expect(onSaveSelection).not.toHaveBeenCalled()
    expect(onApplyRecommended).not.toHaveBeenCalled()
    // the configured (missing) model stays the selection and is called out —
    // never substituted and never silently re-resolved
    expect(screen.getByText(/"gone:model" is not installed — Cozmo cannot use it until it is\./)).toBeTruthy()
    expect(screen.queryByText('Using recommended')).toBeNull()
  })

  it('per-workload Use Recommended remains intact', () => {
    const onApplyRecommended = vi.fn().mockResolvedValue({ ok: true })
    renderPage({ onApplyRecommended })
    fireEvent.click(selectionSection().getByTitle('Use the recommended model for Deep Research'))
    expect(onApplyRecommended).toHaveBeenCalledTimes(1)
    expect(onApplyRecommended).toHaveBeenCalledWith(['research'])
  })

  it('recommendation changes never silently change the selection', () => {
    const onSaveSelection = vi.fn()
    const onApplyRecommended = vi.fn().mockResolvedValue({ ok: true })
    renderPage({ discovery: DIFFERENT, onSaveSelection, onApplyRecommended })
    expect(onSaveSelection).not.toHaveBeenCalled()
    expect(onApplyRecommended).not.toHaveBeenCalled()
    expect((selectionSection().getByTitle('Choose the model for General') as HTMLSelectElement).value)
      .toBe('qwen2.5-coder:7b')
  })
})

describe('ModelsSettings — recommendation explanation', () => {
  const explanationFixture = (overrides: Partial<RecommendationExplanation> = {}): RecommendationExplanation => ({
    provenance: { source: 'runtime', confidence: 0.95 },
    hardwareFit: { fit: 'fits', confidence: 'high', strength: 'strong', basis: ['explicit memory requirement'] },
    alternatives: [],
    provisional: false,
    ...overrides,
  })

  const withExplanation = (overrides: Partial<RecommendationExplanation> = {}): DiscoveryPayload => ({
    ...DISCOVERY,
    recommended: {
      ...DISCOVERY.recommended,
      workloads: {
        general: { ...RECOMMENDED.workloads.general, capability: 'chat', explanation: explanationFixture(overrides) },
        research: { ...RECOMMENDED.workloads.research, capability: 'chat', explanation: explanationFixture() },
        code: { ...RECOMMENDED.workloads.code, capability: 'code', explanation: explanationFixture() },
      },
    },
  })

  const expandWhyFor = (label: string) => {
    fireEvent.click(selectionSection().getByTitle(new RegExp(`Why is .*${label}`)))
  }

  it('renders a "Why this model?" control on every recommended workload row', () => {
    renderPage({ discovery: withExplanation() })
    expect(selectionSection().getAllByTitle(/Why is/)).toHaveLength(3)
  })

  it('is collapsed by default', () => {
    renderPage({ discovery: withExplanation() })
    expect(screen.queryByLabelText('Why this model for General')).toBeNull()
  })

  it('expanding reveals capability provenance', () => {
    renderPage({ discovery: withExplanation() })
    expandWhyFor('General')
    const panel = within(screen.getByLabelText('Why this model for General'))
    expect(panel.getByText('Why this model?')).toBeTruthy()
    expect(panel.getByText('Capability')).toBeTruthy()
    expect(panel.getByText('chat')).toBeTruthy()
  })

  it('runtime provenance renders as Runtime evidence', () => {
    renderPage({ discovery: withExplanation() })
    expandWhyFor('General')
    const panel = within(screen.getByLabelText('Why this model for General'))
    expect(panel.getByText(/Runtime evidence/)).toBeTruthy()
  })

  it('curated provenance renders as Curated evidence', () => {
    renderPage({ discovery: withExplanation({ provenance: { source: 'seed', confidence: 0.9 } }) })
    expandWhyFor('General')
    const panel = within(screen.getByLabelText('Why this model for General'))
    expect(panel.getByText(/Curated evidence/)).toBeTruthy()
  })

  it('name-inference provenance is presented as a weak hint', () => {
    renderPage({ discovery: withExplanation({ provenance: { source: 'name-inference', confidence: 0.7 } }) })
    expandWhyFor('General')
    const panel = within(screen.getByLabelText('Why this model for General'))
    expect(panel.getByText(/Name-based hint/)).toBeTruthy()
    expect(panel.getByText(/(weak)/)).toBeTruthy()
  })

  it('renders the hardware-fit contribution', () => {
    renderPage({ discovery: withExplanation() })
    expandWhyFor('General')
    const panel = within(screen.getByLabelText('Why this model for General'))
    expect(panel.getByText('Hardware')).toBeTruthy()
    expect(panel.getByText('Fits detected hardware')).toBeTruthy()
    expect(panel.getByText(/high confidence/)).toBeTruthy()
  })

  it('renders provisional uncertainty', () => {
    renderPage({ discovery: withExplanation({ provisional: true }) })
    expandWhyFor('General')
    const panel = within(screen.getByLabelText('Why this model for General'))
    expect(panel.getByText(/Provisional recommendation/)).toBeTruthy()
    expect(panel.getByText(/some hardware\/model information is uncertain/i)).toBeTruthy()
  })

  it('renders the qualification independently', () => {
    renderPage({ discovery: withExplanation() })
    expandWhyFor('General')
    const panel = within(screen.getByLabelText('Why this model for General'))
    expect(panel.getByText('Qualification')).toBeTruthy()
    expect(panel.getByText('Supported')).toBeTruthy()
  })

  it('renders viable alternatives from the backend', () => {
    const alt = {
      model: 'qwen2.5-coder:7b',
      fit: 'fits',
      strength: 'runtime',
      capability: 'chat',
      qualification: 'supported',
      reasons: ['runtime capability evidence'],
    }
    renderPage({ discovery: withExplanation({ alternatives: [alt] }) })
    expandWhyFor('General')
    const panel = within(screen.getByLabelText('Why this model for General'))
    expect(panel.getByText('Other good options')).toBeTruthy()
    expect(panel.getByText('qwen2.5-coder:7b')).toBeTruthy()
    expect(panel.getAllByText(/Runtime evidence/).length).toBeGreaterThanOrEqual(1)
  })

  it('alternatives never replace the winner', () => {
    const alt = {
      model: 'qwen2.5-coder:7b',
      fit: 'fits',
      strength: 'runtime',
      capability: 'chat',
      qualification: 'supported',
      reasons: ['runtime capability evidence'],
    }
    renderPage({ discovery: withExplanation({ alternatives: [alt] }) })
    expandWhyFor('General')
    const panel = within(screen.getByLabelText('Why this model for General'))
    // header still points at the winner, alternative only listed below
    expect(panel.getByText(/Recommended:/)).toBeTruthy()
    expect(panel.getAllByText('llama3.1:8b').length).toBeGreaterThanOrEqual(1)
    expect(panel.getByText('qwen2.5-coder:7b')).toBeTruthy()
  })

  it('keeps recommended and selected models visually distinct', () => {
    const discovery = {
      ...withExplanation(),
      workloads: { general: 'qwen2.5-coder:7b', research: '', code: '' },
    }
    renderPage({ discovery })
    expandWhyFor('General')
    const panel = within(screen.getByLabelText('Why this model for General'))
    expect(panel.getByText(/Recommended:/)).toBeTruthy()
    expect(panel.getByText(/Selected:/)).toBeTruthy()
    expect(panel.getAllByText('llama3.1:8b').length).toBeGreaterThanOrEqual(1)
    expect(panel.getByText('qwen2.5-coder:7b')).toBeTruthy()
    // no implication that the recommendation already changed the selection
    const section = selectionSection()
    expect(section.getByText('Recommendation changed')).toBeTruthy()
    expect(section.getByTitle('Use the recommended model for General')).toBeTruthy()
  })

  it('expanding the explanation never modifies configuration', () => {
    const onSaveSelection = vi.fn()
    const onApplyRecommended = vi.fn()
    renderPage({ discovery: withExplanation(), onSaveSelection, onApplyRecommended })
    expandWhyFor('General')
    expect(onSaveSelection).not.toHaveBeenCalled()
    expect(onApplyRecommended).not.toHaveBeenCalled()
  })

  it('no explanation data produces no fabricated reasons', () => {
    renderPage({ discovery: DISCOVERY })  // recommendations without explanation
    expect(selectionSection().queryByTitle(/Why is/)).toBeNull()
    expect(screen.queryByText('Why it scored well')).toBeNull()
    expect(screen.queryByText('Provisional recommendation')).toBeNull()
  })

  it('unknown provenance renders as Unknown, never fabricated', () => {
    renderPage({ discovery: withExplanation({ provenance: null }) })
    expandWhyFor('General')
    const panel = within(screen.getByLabelText('Why this model for General'))
    expect(panel.getByText(/Unknown/)).toBeTruthy()
  })

  it('no alternatives shows the explicit empty state', () => {
    renderPage({ discovery: withExplanation({ alternatives: [] }) })
    expandWhyFor('General')
    const panel = within(screen.getByLabelText('Why this model for General'))
    expect(panel.getByText('No other verified alternatives found.')).toBeTruthy()
  })
})

describe('ModelsSettings — hardware intelligence surface', () => {
  const hardwareSection = () => within(screen.getByLabelText('System hardware'))

  it('renders detected GPU, VRAM, system RAM and detection confidence', () => {
    renderPage()
    const hw = hardwareSection()
    expect(hw.getByText('NVIDIA GeForce RTX 4060')).toBeTruthy()
    expect(hw.getByText('8 GB')).toBeTruthy()
    expect(hw.getByText('16 GB')).toBeTruthy()
    expect(hw.getByText('High confidence')).toBeTruthy()
  })

  it('labels each hardware fact without overstating certainty', () => {
    renderPage()
    const hw = hardwareSection()
    expect(hw.getByText(/GPU:/)).toBeTruthy()
    expect(hw.getByText(/VRAM:/)).toBeTruthy()
    expect(hw.getByText(/System RAM:/)).toBeTruthy()
    expect(hw.getByText(/Detection:/)).toBeTruthy()
  })

  it('renders the detection confidence exactly as reported by the backend', () => {
    renderPage({ discovery: { ...DISCOVERY, hardware: { ...DISCOVERY.hardware, confidence: 'low' } } })
    expect(hardwareSection().getByText('Low confidence')).toBeTruthy()
  })

  it('renders medium detection confidence as reported', () => {
    renderPage({ discovery: { ...DISCOVERY, hardware: { ...DISCOVERY.hardware, confidence: 'medium' } } })
    expect(hardwareSection().getByText('Medium confidence')).toBeTruthy()
  })

  it('unknown hardware renders visibly unknown, never inferred', () => {
    renderPage({
      discovery: {
        ...DISCOVERY,
        hardware: { ramGb: 0, gpu: { name: '', vramTotalGb: null, vendor: '' }, confidence: 'unknown' },
      },
    })
    const hw = hardwareSection()
    expect(hw.getAllByText(/Unknown/).length).toBeGreaterThanOrEqual(4)
    expect(hw.queryByText(/\d+ GB/)).toBeNull()
  })

  it('partial hardware leaves VRAM visibly unknown, not a guess', () => {
    renderPage({
      discovery: {
        ...DISCOVERY,
        hardware: { ramGb: 16, gpu: { name: 'NVIDIA GeForce RTX 4060', vramTotalGb: null, vendor: 'nvidia' }, confidence: 'medium' },
      },
    })
    const hw = hardwareSection()
    expect(hw.getByText('NVIDIA GeForce RTX 4060')).toBeTruthy()
    expect(hw.getAllByText('Unknown').length).toBeGreaterThanOrEqual(1)
    expect(hw.queryByText(/\d+ GB/)).toBeTruthy()   // RAM only — never a VRAM guess
  })

  it('provisional recommendations surface the partial-hardware warning', () => {
    renderPage({ discovery: { ...DISCOVERY, recommended: { ...DISCOVERY.recommended, provisional: true } } })
    const hw = hardwareSection()
    expect(hw.getByText(/provisional until detection improves/)).toBeTruthy()
    expect(hw.getByText(/Some hardware details are unknown/i)).toBeTruthy()
  })

  it('non-provisional recommendations show no partial-hardware warning', () => {
    renderPage()
    expect(screen.queryByText(/provisional until detection improves/)).toBeNull()
  })
})

describe('ModelsSettings — recommendation UX refinement', () => {
  const recommendedSection = () => within(screen.getByLabelText('Recommended models'))

  const explanationFixture = (overrides: Partial<RecommendationExplanation> = {}): RecommendationExplanation => ({
    provenance: { source: 'runtime', confidence: 0.95 },
    hardwareFit: { fit: 'fits', confidence: 'high', strength: 'strong', basis: ['explicit memory requirement'] },
    alternatives: [],
    provisional: false,
    ...overrides,
  })

  const withExplanation = (overrides: Partial<RecommendationExplanation> = {}): DiscoveryPayload => ({
    ...DISCOVERY,
    recommended: {
      ...DISCOVERY.recommended,
      workloads: {
        general: { ...RECOMMENDED.workloads.general, capability: 'chat', explanation: explanationFixture(overrides) },
        research: { ...RECOMMENDED.workloads.research, explanation: explanationFixture() },
        code: { ...RECOMMENDED.workloads.code, explanation: explanationFixture() },
      },
    },
  })

  const expandWhyFor = (label: string) => {
    fireEvent.click(selectionSection().getByTitle(new RegExp(`Why is .*${label}`)))
  }

  it('recommends but keeps the model clearly advisory', () => {
    renderPage()
    const rec = recommendedSection()
    expect(rec.getByText(/you control the actual selected model/)).toBeTruthy()
    expect(rec.getByText(/never changes your selection/)).toBeTruthy()
    expect(rec.getByText(/only the .Use Recommended. action does/)).toBeTruthy()
  })

  it('removes "Using recommended" when the user picks another model', () => {
    const onSaveSelection = vi.fn().mockResolvedValue({ ok: true })
    const { rerender } = renderPage({ discovery: USING_RECOMMENDED, onSaveSelection })
    const section = selectionSection()
    expect(section.getAllByText('Using recommended')).toHaveLength(3)
    fireEvent.change(section.getByTitle('Choose the model for General'), {
      target: { value: 'qwen2.5-coder:7b' },
    })
    expect(onSaveSelection).toHaveBeenCalledWith({
      general: 'qwen2.5-coder:7b',
      research: 'llama3.1:8b',
      code: 'qwen2.5-coder:7b',
    })
    rerender(
      <ModelsSettings
        discovery={{
          ...USING_RECOMMENDED,
          workloads: { general: 'qwen2.5-coder:7b', research: 'llama3.1:8b', code: 'qwen2.5-coder:7b' },
        }}
        schema={SCHEMA}
        installing={{}}
        onInstall={vi.fn()}
        onDelete={vi.fn().mockResolvedValue(true)}
        onRefresh={vi.fn()}
        loading={false}
        onSaveSelection={onSaveSelection}
        onApplyRecommended={vi.fn().mockResolvedValue({ ok: true })}
      />
    )
    const section2 = selectionSection()
    expect(section2.getAllByText('Using recommended')).toHaveLength(2)
    expect(section2.getByText('Recommendation changed')).toBeTruthy()
    expect(section2.getByTitle('Use the recommended model for General')).toBeTruthy()
  })

  it('hides the redundant per-row Use Recommended once already using it', () => {
    renderPage({ discovery: USING_RECOMMENDED })
    const section = selectionSection()
    expect(section.getAllByText('Using recommended')).toHaveLength(3)
    expect(section.queryByTitle(/Use the recommended model/)).toBeNull()
  })

  it('keeps "None selected" visibly unset — the only "nothing chosen" state', () => {
    renderPage()
    const section = selectionSection()
    const sel = section.getByTitle('Choose the model for General') as HTMLSelectElement
    expect(sel.value).toBe('')
    expect(section.getAllByText('None selected')).toHaveLength(3)
    const blank = Array.from(sel.querySelectorAll('option')).find((o) => o.value === '')
    expect(blank?.textContent).toBe('None selected')
  })

  it('shows Recommendation confidence in the why-this-model panel', () => {
    renderPage({ discovery: withExplanation({}) })
    expandWhyFor('General')
    const panel = within(screen.getByLabelText('Why this model for General'))
    expect(panel.getByText('Recommendation confidence: High')).toBeTruthy()
  })

  it('alternatives stay advisory and never become selection options', () => {
    const alt = {
      model: 'falcon3:10b',
      fit: 'fits',
      strength: 'runtime',
      capability: 'chat',
      qualification: 'supported',
      reasons: ['runtime capability evidence'],
    }
    renderPage({ discovery: withExplanation({ alternatives: [alt] }) })
    expandWhyFor('General')
    const panel = within(screen.getByLabelText('Why this model for General'))
    expect(panel.getByText('falcon3:10b')).toBeTruthy()
    const optionValues = Array.from(document.querySelectorAll('select option'))
      .map((o) => (o as HTMLOptionElement).value)
    expect(optionValues).not.toContain('falcon3:10b')
  })
})

describe('ModelsSettings — selection integrity (Phase 6 contract)', () => {
  const gemma = 'gemma4:e4b'
  const qwen3 = 'qwen3:8b'
  const coder = 'qwen2.5-coder:1.5b'

  const realistic = (workloads: Record<string, string>, recWorkloads?: Record<string, WorkloadRecommendation>): DiscoveryPayload => ({
    ...DISCOVERY,
    models: [
      installed(gemma, 'Gemma 4 4B'),
      installed(coder, 'Qwen 2.5 Coder 1.5B'),
    ],
    installedNames: [gemma, coder],
    workloads,
    recommended: {
      workloads: {
        general: rec('general', gemma),
        research: rec('research', qwen3),
        code: rec('code', coder),
        ...(recWorkloads ?? {}),
      },
      provisional: false,
    },
  })

  const rowFor = (label: string) => {
    const section = selectionSection()
    const select = section.getByTitle(`Choose the model for ${label}`) as HTMLSelectElement
    return { section, select }
  }

  it('scenario 10: per-workload badge truth table against diverging recommendations', () => {
    renderPage({ discovery: realistic({ general: gemma, research: gemma, code: coder }) })
    const section = selectionSection()
    // general: selected === recommended -> Using recommended
    // research: selected (gemma) !== recommended (qwen3) -> Recommendation changed + Use Recommended
    // code: selected === recommended -> Using recommended
    expect(section.getAllByText('Using recommended')).toHaveLength(2)
    expect(section.getByText('Recommendation changed')).toBeTruthy()
    expect(within(section.getByText('Recommendation changed').closest('div')!)
      .getByText('Deep Research')).toBeTruthy()
    expect(section.getByTitle('Use the recommended model for Deep Research')).toBeTruthy()
    expect(section.queryByTitle('Use the recommended model for General')).toBeNull()
    expect(section.queryByTitle('Use the recommended model for Code')).toBeNull()
  })

  it('scenario 10: dropdowns display the persisted selection per workload, ignoring recommendations', () => {
    renderPage({ discovery: realistic({ general: gemma, research: gemma, code: coder }) })
    expect(rowFor('General').select.value).toBe(gemma)
    expect(rowFor('Deep Research').select.value).toBe(gemma)   // NOT qwen3
    expect(rowFor('Code').select.value).toBe(coder)
  })

  it('Using recommended appears iff selection === recommendation, per workload', () => {
    renderPage({ discovery: realistic({ general: gemma, research: qwen3, code: coder }) })
    const section = selectionSection()
    expect(section.getAllByText('Using recommended')).toHaveLength(3)
    expect(section.queryByText('Recommendation changed')).toBeNull()
  })

  it('an explicit selected model stays the dropdown value even when the recommendation differs', () => {
    renderPage({ discovery: realistic({ general: coder, research: gemma, code: coder }) })
    const { select, section } = rowFor('General')
    expect(select.value).toBe(coder)
    expect(section.getAllByText('Recommendation changed').length).toBeGreaterThanOrEqual(1)
    expect(section.getByTitle('Use the recommended model for General')).toBeTruthy()
  })

  it('a recommendation changing from A to B never changes the selected model A', () => {
    // selection fixed on gemma; the recommendation for research is qwen3 (differs) — selection must NOT move
    renderPage({ discovery: realistic({ general: gemma, research: gemma, code: coder }) })
    expect(rowFor('Deep Research').select.value).toBe(gemma)
  })

  it('reloading discovery/recommendations never changes the selected model', () => {
    const { rerender } = renderPage({ discovery: realistic({ general: gemma, research: gemma, code: coder }) })
    expect(rowFor('Deep Research').select.value).toBe(gemma)
    rerender(
      <ModelsSettings
        discovery={realistic({ general: gemma, research: gemma, code: coder }, { research: { ...RECOMMENDED.workloads.research, model: gemma } })}
        schema={SCHEMA}
        installing={{}}
        onInstall={vi.fn()}
        onDelete={vi.fn().mockResolvedValue(true)}
        onRefresh={vi.fn()}
        loading={false}
        onSaveSelection={vi.fn().mockResolvedValue({ ok: true })}
        onApplyRecommended={vi.fn().mockResolvedValue({ ok: true })}
      />
    )
    // recommendation now equals the selection -> Using recommended flips on; selection itself unchanged
    expect(rowFor('Deep Research').select.value).toBe(gemma)
    expect(selectionSection().getAllByText('Using recommended')).toHaveLength(3)
  })

  it('dropdown displays the persisted selection even when it is missing from the installed list', () => {
    const disc = realistic({ general: gemma, research: qwen3, code: coder })
    // qwen3 is selected but not installed -> must remain visible as the dropdown value
    renderPage({ discovery: disc })
    const { select, section } = rowFor('Deep Research')
    expect(select.value).toBe(qwen3)
    const opt = Array.from(select.querySelectorAll('option')).find((o) => o.value === qwen3)
    expect(opt).toBeTruthy()
    expect(opt?.textContent).toBe('qwen3:8b')
    expect(section.getByText(/"qwen3:8b" is not installed — Cozmo cannot use it until it is\./)).toBeTruthy()
    // selection still equals recommendation -> Using recommended (missing but selected)
    expect(section.getAllByText('Using recommended').length).toBeGreaterThanOrEqual(1)
  })

  it('dropdown with a missing selected model never forces a value change or implicit selection', () => {
    const onSaveSelection = vi.fn()
    const onApplyRecommended = vi.fn()
    renderPage({ discovery: realistic({ general: gemma, research: qwen3, code: coder }), onSaveSelection, onApplyRecommended })
    expect(rowFor('Deep Research').select.value).toBe(qwen3)
    expect(onSaveSelection).not.toHaveBeenCalled()
    expect(onApplyRecommended).not.toHaveBeenCalled()
  })

  it('workload-key mapping is exact: general/research/code, labels are display-only', () => {
    renderPage({ discovery: realistic({ general: gemma, research: qwen3, code: coder }) })
    expect(rowFor('General').select.value).toBe(gemma)
    expect(rowFor('Deep Research').select.value).toBe(qwen3)
    expect(rowFor('Code').select.value).toBe(coder)
  })

  it('empty selection shows None selected with no derived indicator', () => {
    renderPage({ discovery: realistic({ general: '', research: '', code: '' }) })
    const section = selectionSection()
    expect(section.getAllByText('None selected')).toHaveLength(3)
    expect(section.queryByText('Using recommended')).toBeNull()
    expect(section.queryByText('Recommendation changed')).toBeNull()
    expect(section.queryByText('No recommendation available')).toBeNull()
  })
})

describe('ModelsSettings — model removal', () => {
  const missing = (name: string, displayName: string): DiscoveredModelEntry => ({
    name,
    displayName,
    status: 'missing',
    size: null,
    capabilities: {},
    recommended: true,
    tier: 'supported',
    qualification: 'trusted',
    reasons: [],
    caveats: [],
    approxRamGb: null,
    eligibility: { hardwareFit: 'unknown', hardwareConfidence: 'unknown' },
  })

  it('shows Remove only for installed models', () => {
    renderPage()
    const section = librarySection()
    expect(section.getAllByTitle(/^Remove /)).toHaveLength(2)
    expect(section.getByTitle('Remove llama3.1:8b')).toBeTruthy()
    expect(section.getByTitle('Remove qwen2.5-coder:7b')).toBeTruthy()
    // the missing model has no Remove — only an explicit Install action
    expect(section.getAllByRole('button', { name: /install/i })).toHaveLength(1)
  })

  it('requires confirmation before removing', () => {
    const onDelete = vi.fn().mockResolvedValue(true)
    renderPage({ onDelete })
    fireEvent.click(librarySection().getByTitle('Remove llama3.1:8b'))
    const section = librarySection()
    expect(section.getByText('Remove llama3.1:8b?')).toBeTruthy()
    expect(section.getByText(/will not change your workload selections/)).toBeTruthy()
    expect(onDelete).not.toHaveBeenCalled()
  })

  it('Cancel dismisses the confirmation without deleting', () => {
    const onDelete = vi.fn().mockResolvedValue(true)
    renderPage({ onDelete })
    fireEvent.click(librarySection().getByTitle('Remove llama3.1:8b'))
    const section = librarySection()
    fireEvent.click(section.getByRole('button', { name: /cancel/i }))
    expect(onDelete).not.toHaveBeenCalled()
    expect(section.queryByText('Remove llama3.1:8b?')).toBeNull()
    expect(section.getByTitle('Remove llama3.1:8b')).toBeTruthy()
  })

  it('Confirm calls the delete action with the model name', async () => {
    const onDelete = vi.fn().mockResolvedValue(true)
    renderPage({ onDelete })
    fireEvent.click(librarySection().getByTitle('Remove llama3.1:8b'))
    fireEvent.click(librarySection().getByTitle('Confirm remove llama3.1:8b'))
    await waitFor(() => expect(onDelete).toHaveBeenCalledTimes(1))
    expect(onDelete).toHaveBeenCalledWith('llama3.1:8b')
  })

  it('after successful deletion a refreshed library no longer shows the model installed', async () => {
    const onDelete = vi.fn().mockResolvedValue(true)
    const { rerender } = renderPage({ onDelete })
    fireEvent.click(librarySection().getByTitle('Remove llama3.1:8b'))
    fireEvent.click(librarySection().getByTitle('Confirm remove llama3.1:8b'))
    await waitFor(() => expect(onDelete).toHaveBeenCalledWith('llama3.1:8b'))
    rerender(
      <ModelsSettings
        discovery={{
          ...DISCOVERY,
          installedNames: ['qwen2.5-coder:7b'],
          missingModels: ['nomic-embed-text', 'llama3.1:8b'],
          models: [
            installed('qwen2.5-coder:7b', 'Qwen 2.5 Coder 7B'),
            missing('llama3.1:8b', 'Llama 3.1 8B'),
            missing('nomic-embed-text', 'Nomic Embed Text'),
          ],
        }}
        schema={SCHEMA}
        installing={{}}
        onInstall={vi.fn()}
        onDelete={onDelete}
        onRefresh={vi.fn()}
        loading={false}
        onSaveSelection={vi.fn().mockResolvedValue({ ok: true })}
        onApplyRecommended={vi.fn().mockResolvedValue({ ok: true })}
      />
    )
    const section = librarySection()
    expect(section.queryByTitle('Remove llama3.1:8b')).toBeNull()
    expect(section.getByTitle('Remove qwen2.5-coder:7b')).toBeTruthy()
  })

  it('a selected-but-deleted model stays selected and shows the missing warning', () => {
    const onSaveSelection = vi.fn()
    const onApplyRecommended = vi.fn()
    renderPage({
      discovery: {
        ...DISCOVERY,
        installedNames: ['qwen2.5-coder:7b'],
        missingModels: ['nomic-embed-text', 'llama3.1:8b'],
        workloads: { general: 'llama3.1:8b', research: '', code: '' },
        models: [
          installed('qwen2.5-coder:7b', 'Qwen 2.5 Coder 7B'),
          missing('llama3.1:8b', 'Llama 3.1 8B'),
          missing('nomic-embed-text', 'Nomic Embed Text'),
        ],
      },
      onSaveSelection,
      onApplyRecommended,
    })
    const section = selectionSection()
    // the configured (still-selected) model is called out as missing — the
    // selection is never rewritten, substituted, or cleared
    expect(section.getByText(/"llama3.1:8b" is not installed — Cozmo cannot use it until it is\./)).toBeTruthy()
    // nothing was auto-selected, auto-applied, or substituted
    expect(onSaveSelection).not.toHaveBeenCalled()
    expect(onApplyRecommended).not.toHaveBeenCalled()
    expect(section.queryByText('Recommendation changed')).toBeNull()
  })
})