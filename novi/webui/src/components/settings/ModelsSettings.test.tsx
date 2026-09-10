import { describe, it, expect, vi } from 'vitest'
import { render, screen, fireEvent, within } from '@testing-library/react'
import { ModelsSettings } from './ModelsSettings'
import type { DiscoveryPayload, DiscoveredModelEntry, SchemaResponse, PrimaryRecommendation } from './api'

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

function primaryRec(model: string, extra: Partial<PrimaryRecommendation> = {}): PrimaryRecommendation {
  return {
    model,
    qualification: 'supported',
    hardwareConfidence: 'high',
    reasons: ['Tested with Novi'],
    caveats: [],
    capabilities: [],
    visionCapable: false,
    ...extra,
  }
}

const BASE: DiscoveryPayload = {
  hardware: { ramGb: 16, gpu: { name: 'NVIDIA GeForce RTX 4060', vramTotalGb: 8, vendor: 'nvidia' }, confidence: 'high' },
  models: [
    installed('llama3.1:8b', 'Llama 3.1 8B'),
    installed('qwen2.5-coder:7b', 'Qwen 2.5 Coder 7B'),
  ],
  missingModels: [],
  installedNames: ['llama3.1:8b', 'qwen2.5-coder:7b'],
  dismissedRecommended: [],
  model: '',
  primary: '',
  recommended: { primary: primaryRec('llama3.1:8b'), provisional: false },
  vision_capable: false,
  capabilities: { vision: false, tools: true, reasoning: true, thinking: true, audio: false, coding: false },
}

const SCHEMA: SchemaResponse = { settings: [], groups: [] }

function renderPage(props?: {
  discovery?: DiscoveryPayload
  schema?: SchemaResponse
  embeddingModel?: string
  onSaveSelection?: (model: string) => Promise<{ ok: boolean; error?: string }>
  onApplyRecommended?: () => Promise<{ ok: boolean; error?: string }>
}) {
  return render(
    <ModelsSettings
      discovery={props?.discovery ?? BASE}
      schema={props?.schema ?? SCHEMA}
      embeddingModel={props?.embeddingModel}
      installing={{}}
      onInstall={vi.fn()}
      onDelete={vi.fn().mockResolvedValue(true)}
      onRefresh={vi.fn()}
      loading={false}
      onSaveSelection={props?.onSaveSelection ?? vi.fn().mockResolvedValue({ ok: true })}
      onApplyRecommended={props?.onApplyRecommended ?? vi.fn().mockResolvedValue({ ok: true })}
    />
  )
}

function selectionSection() {
  return within(screen.getByLabelText('Current selection'))
}

describe('ModelsSettings - single primary surface', () => {
  it('renders exactly one primary selector, not three workload selectors', () => {
    renderPage()
    const section = selectionSection()
    expect(section.getAllByText('Model').length).toBeGreaterThanOrEqual(1)
    const selects = section.getAllByRole('combobox')
    expect(selects).toHaveLength(1)
    // No legacy workload labels/selectors
    expect(screen.queryByText('General')).toBeNull()
    expect(screen.queryByText('Deep Research')).toBeNull()
    expect(screen.queryByText('Code')).toBeNull()
    expect(document.querySelector('[data-workload]')).toBeNull()
  })

  it('persists the primary model verbatim when the selector changes', () => {
    const onSaveSelection = vi.fn().mockResolvedValue({ ok: true })
    renderPage({ onSaveSelection })
    const select = selectionSection().getByRole('combobox') as HTMLSelectElement
    fireEvent.change(select, { target: { value: 'qwen2.5-coder:7b' } })
    expect(onSaveSelection).toHaveBeenCalledTimes(1)
    expect(onSaveSelection).toHaveBeenCalledWith('qwen2.5-coder:7b')
  })

  it('shows the advisory Recommended line with capability chips for the primary model', () => {
    const discovery: DiscoveryPayload = {
      ...BASE,
      primary: '',
      model: '',
      capabilities: { vision: true, tools: true, reasoning: true, thinking: true, audio: false, coding: true },
      recommended: {
        primary: primaryRec('llama3.1:8b', { capabilities: ['vision', 'tools'] }),
        provisional: false,
      },
    }
    renderPage({ discovery })
    const section = selectionSection()
    expect(section.getByText(/Recommended:/)).toBeTruthy()
    expect(section.getByText('llama3.1:8b')).toBeTruthy()
    // Capability chips render for supported caps (Vision/Tools/Thinking)
    expect(section.getByText('Vision')).toBeTruthy()
    expect(section.getByText('Tools')).toBeTruthy()
  })

  it('Use Recommended applies the single advisory recommendation', () => {
    const onApplyRecommended = vi.fn().mockResolvedValue({ ok: true })
    renderPage({ onApplyRecommended })
    fireEvent.click(selectionSection().getByTitle('Use the recommended model'))
    expect(onApplyRecommended).toHaveBeenCalledTimes(1)
    expect(onApplyRecommended.mock.calls[0]).toEqual([])
  })

  it('shows Using recommended iff selection === recommendation', () => {
    renderPage({
      discovery: { ...BASE, primary: 'llama3.1:8b', model: 'llama3.1:8b' },
    })
    expect(selectionSection().getByText('Using recommended')).toBeTruthy()
  })

  it('shows Recommendation changed when selection diverges', () => {
    renderPage({
      discovery: { ...BASE, primary: 'qwen2.5-coder:7b', model: 'qwen2.5-coder:7b' },
    })
    const section = selectionSection()
    expect(section.getByText('Recommendation changed')).toBeTruthy()
    expect(section.getByTitle('Use the recommended model')).toBeTruthy()
  })

  it('hides per-row Use Recommended once already using it', () => {
    renderPage({
      discovery: { ...BASE, primary: 'llama3.1:8b', model: 'llama3.1:8b' },
    })
    expect(selectionSection().queryByTitle('Use the recommended model')).toBeNull()
  })

  it('shows No recommendation available when primary rec is empty', () => {
    renderPage({
      discovery: { ...BASE, recommended: { primary: null, provisional: true } },
    })
    expect(selectionSection().getByText('No recommendation available')).toBeTruthy()
    expect(selectionSection().queryByTitle('Use the recommended model')).toBeNull()
  })

  it('reports a selected-but-missing model without substituting', () => {
    renderPage({
      discovery: {
        ...BASE,
        primary: 'gone:model',
        model: 'gone:model',
        models: [...BASE.models, { ...installed('gone:model', 'Gone Model'), status: 'missing' } as DiscoveredModelEntry],
      },
    })
    expect(selectionSection().getByText(/"gone:model" is not installed/)).toBeTruthy()
  })

  it('renders Why this model explanation for the primary recommendation', () => {
    const discovery: DiscoveryPayload = {
      ...BASE,
      recommended: {
        primary: primaryRec('llama3.1:8b', {
          explanation: {
            provenance: { source: 'runtime', confidence: 0.9 },
            hardwareFit: { fit: 'fits', confidence: 'high', strength: 'strong', basis: ['ram'] },
            alternatives: [],
            provisional: false,
          },
        }),
        provisional: false,
      },
    }
    const onSaveSelection = vi.fn().mockResolvedValue({ ok: true })
    renderPage({ discovery, onSaveSelection })
    fireEvent.click(selectionSection().getByText(/Why this model\?/))
    expect(screen.getByLabelText('Why this model')).toBeTruthy()
    expect(onSaveSelection).not.toHaveBeenCalled()
  })

  it('surfaces install/dismiss consent for recommended-but-missing models', () => {
    const missing: DiscoveredModelEntry = {
      ...installed('qwen2.5vl:7b', 'Qwen 2.5 VL 7B'),
      status: 'available',
      capabilities: { chat: true, vision: true },
    }
    renderPage({ discovery: { ...BASE, models: [...BASE.models, missing] } })
    const setup = within(screen.getByLabelText('Recommended model setup'))
    expect(setup.getByText('Recommended model unavailable')).toBeTruthy()
    expect(setup.getByText('Qwen 2.5 VL 7B')).toBeTruthy()
  })
})

describe('ModelsSettings - memory embedding model (read-only)', () => {
  it('shows embedding model', () => {
    renderPage()
    const section = within(screen.getByLabelText('Memory embedding model'))
    expect(screen.getByText(/nomic-embed-text/)).toBeTruthy()
  })

  it('prefers the live embedding.model value over the schema default', () => {
    renderPage({ embeddingModel: 'mxbai-embed-large' })
    const section = within(screen.getByLabelText('Memory embedding model'))
    expect(section.getByText('mxbai-embed-large')).toBeTruthy()
  })

  it('shows installed status when the embedding model is installed', () => {
    const emb: DiscoveredModelEntry = {
      ...installed('nomic-embed-text:v1.5', 'Nomic Embed Text v1.5'),
      capabilities: { embeddings: true },
    }
    renderPage({
      discovery: {
        ...BASE,
        models: [...BASE.models, emb],
        installedNames: [...BASE.installedNames, 'nomic-embed-text:v1.5'],
      },
    })
    const section = within(screen.getByLabelText('Memory embedding model'))
    expect(section.getByText('installed')).toBeTruthy()
  })

  it('shows a missing hint when the embedding model is not installed', () => {
    renderPage()
    const section = within(screen.getByLabelText('Memory embedding model'))
    expect(section.getByText('missing')).toBeTruthy()
    expect(section.getByText(/Not installed — check General/)).toBeTruthy()
  })

  it('keeps the embedding model out of the primary chat-model dropdown', () => {
    const emb: DiscoveredModelEntry = {
      ...installed('nomic-embed-text:v1.5', 'Nomic Embed Text v1.5'),
      capabilities: { embeddings: true },
    }
    renderPage({
      discovery: {
        ...BASE,
        models: [...BASE.models, emb],
        installedNames: [...BASE.installedNames, 'nomic-embed-text:v1.5'],
      },
    })
    const select = selectionSection().getByRole('combobox') as HTMLSelectElement
    const options = Array.from(select.options).map((o) => o.value)
    expect(options).not.toContain('nomic-embed-text:v1.5')
    // Chat models still selectable
    expect(options).toContain('llama3.1:8b')
    expect(options).toContain('qwen2.5-coder:7b')
  })
})
