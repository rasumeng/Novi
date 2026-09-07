import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, fireEvent } from '@testing-library/react'

const mockValues: Record<string, unknown> = {}

const mockDiscovery = {
  hardware: { ramGb: 16, gpu: { name: '', vramTotalGb: null, vendor: '' }, confidence: 'unknown' },
  models: [],
  missingModels: [],
  installedNames: [],
  dismissedRecommended: [],
  primary: '',
  model: '',
  recommended: { primary: null, provisional: true },
  vision_capable: false,
}

const frameworkMock = {
  schema: { settings: [], groups: [] },
  values: mockValues,
  discovery: mockDiscovery as never,
  settingsByCategory: { general: [], models: [], memory: [], skills: [], connectors: [], permissions: [], developer: [] },
  loading: false,
  installs: {},
  set: vi.fn(),
  install: vi.fn().mockResolvedValue(true),
  refreshDiscovery: vi.fn().mockResolvedValue(undefined),
  savePrimaryModel: vi.fn().mockResolvedValue({ ok: true }),
  applyRecommended: vi.fn().mockResolvedValue({ ok: true }),
  removeModel: vi.fn().mockResolvedValue(true),
  reload: vi.fn(),
}

vi.mock('framer-motion', () => {
  const React = require('react')
  return {
    AnimatePresence: ({ children }: { children: React.ReactNode }) => React.createElement(React.Fragment, null, children),
    motion: {
      div: ({ children, ...props }: { children?: React.ReactNode }) =>
        React.createElement('div', props, children),
    },
  }
})

vi.mock('@/hooks/useToast', () => ({
  useToast: () => ({ showError: vi.fn(), showSuccess: vi.fn(), showInfo: vi.fn() }),
}))

vi.mock('@/services/novi', () => ({
  fetchTools: () => Promise.resolve([]),
  fetchSkills: () => Promise.resolve([]),
  fetchKnowledgeOverview: () => Promise.resolve([]),
  fetchMcpStatus: () => Promise.resolve({}),
  uploadSkill: () => Promise.resolve(true),
  createSkill: () => Promise.resolve(true),
  deleteSkill: () => Promise.resolve(true),
}))

vi.mock('./api', async (importOriginal) => {
  const actual = await importOriginal<typeof import('./api')>()
  return { ...actual }
})

vi.mock('@/hooks/useFrameworkSettings', () => ({
  useFrameworkSettings: () => frameworkMock,
}))

import { SettingsModal } from './SettingsModal'

const NAV = ['General', 'Models', 'Memory', 'Skills', 'Connectors', 'Permissions']
// Developer asserted separately per Task 7

function navButtonLabels(): string[] {
  return screen.getAllByRole('button').map((b) => (b.textContent ?? '').trim()).filter(Boolean)
}

describe('SettingsModal navigation (M4 IA)', () => {
  beforeEach(() => {
    frameworkMock.values = { ...mockValues }
    frameworkMock.discovery = mockDiscovery as never
    frameworkMock.installs = {}
  })

  it('exposes all six beta navigation destinations', () => {
    render(<SettingsModal open onClose={vi.fn()} />)
    const labels = navButtonLabels()
    for (const label of NAV) {
      expect(labels).toContain(label)
    }
  })

  it('does not expose Agent as a first-class destination (beta IA)', () => {
    render(<SettingsModal open onClose={vi.fn()} />)
    expect(navButtonLabels()).not.toContain('Agent')
  })

  it('does not expose Advanced as a first-class destination', () => {
    render(<SettingsModal open onClose={vi.fn()} />)
    expect(navButtonLabels()).not.toContain('Advanced')
  })

  it('opens General first and renders it without experience cards', () => {
    render(<SettingsModal open onClose={vi.fn()} />)
    expect(screen.getByText('Novi is running')).toBeTruthy()
    expect(screen.queryByText('Experience')).toBeNull()
    expect(screen.queryByRole('button', { name: /Light|Medium|Heavy|Custom/i })).toBeNull()
  })

  it('navigates to a page and renders its content', () => {
    render(<SettingsModal open onClose={vi.fn()} />)
    fireEvent.click(screen.getAllByRole('button').find((b) => b.textContent === 'Models')!)
    expect(screen.getByText('Model library')).toBeTruthy()
  })

  it('has exactly six beta destinations (Developer asserted separately per Task 7)', () => {
    render(<SettingsModal open onClose={vi.fn()} />)
    // Leave General first so its content-area quick-link buttons don't get
    // counted alongside the sidebar nav buttons.
    fireEvent.click(screen.getAllByRole('button').find((b) => b.textContent === 'Models')!)
    const nav = navButtonLabels().filter((l) => NAV.includes(l))
    expect(nav).toHaveLength(6)
    expect(new Set(nav).size).toBe(6)
  })

  it('keeps Permissions as a destination distinct from Connectors', () => {
    render(<SettingsModal open onClose={vi.fn()} />)
    const labels = navButtonLabels()
    expect(labels).toContain('Permissions')
    expect(labels).toContain('Connectors')
  })

  it('Developer is hidden from beta nav but reachable via search escape hatch', () => {
    render(<SettingsModal open onClose={vi.fn()} />)
    expect(navButtonLabels()).not.toContain('Developer')
    fireEvent.change(screen.getByLabelText('Search settings'), { target: { value: 'developer' } })
    fireEvent.click(screen.getAllByRole('button').find((b) => b.textContent === 'Developer')!)
    expect(screen.getByText('Expert configuration')).toBeTruthy()
    expect(screen.queryByText('Internal model routing')).toBeNull()
    expect(screen.queryByText(/select capabilit/i)).toBeNull()
  })

  it('reveals Developer via localStorage novi_dev=1 escape hatch', () => {
    localStorage.setItem('novi_dev', '1')
    try {
      render(<SettingsModal open onClose={vi.fn()} />)
      expect(navButtonLabels()).toContain('Developer')
    } finally {
      localStorage.removeItem('novi_dev')
    }
  })
})

describe('SettingsModal framework-only (Task 4.1)', () => {
  it('does not expose legacy flush helpers', async () => {
    const mod = await import('./SettingsModal')
    expect((mod as any).legacyPatch).toBeUndefined()
    expect((mod as any).collectLeafPaths).toBeUndefined()
    expect((mod as any).readLeaf).toBeUndefined()
  })
})
