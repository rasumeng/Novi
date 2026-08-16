import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, fireEvent } from '@testing-library/react'

const mockValues: Record<string, unknown> = {}

const mockDiscovery = {
  hardware: { ramGb: 16 },
  models: [],
  missingModels: [],
  installedNames: [],
  workloads: { general: '', research: '', code: '' },
  recommended: { workloads: {}, provisional: true },
  vision_capable: false,
}

const frameworkMock = {
  schema: { settings: [], groups: [] },
  values: mockValues,
  discovery: mockDiscovery as never,
  settingsByCategory: { general: [], models: [], agent: [], memory: [], skills: [], connectors: [], permissions: [], developer: [] },
  loading: false,
  installs: {},
  set: vi.fn(),
  install: vi.fn().mockResolvedValue(true),
  refreshDiscovery: vi.fn().mockResolvedValue(undefined),
  saveWorkloadSelection: vi.fn().mockResolvedValue({ ok: true }),
  applyRecommended: vi.fn().mockResolvedValue({ ok: true }),
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

vi.mock('@/services/cozmo', () => ({
  fetchTools: () => Promise.resolve([]),
  fetchSkills: () => Promise.resolve([]),
  fetchKnowledgeOverview: () => Promise.resolve([]),
  fetchMcpStatus: () => Promise.resolve({}),
  uploadSkill: () => Promise.resolve(true),
  createSkill: () => Promise.resolve(true),
  deleteSkill: () => Promise.resolve(true),
}))

vi.mock('./api', () => ({
  fetchConfig: () => Promise.resolve({ models: {} }),
  saveConfig: () => Promise.resolve(),
}))

vi.mock('@/hooks/useFrameworkSettings', () => ({
  useFrameworkSettings: () => frameworkMock,
}))

import { SettingsModal } from './SettingsModal'

const NAV = ['General', 'Models', 'Agent', 'Memory', 'Skills', 'Connectors', 'Permissions', 'Developer']

function navButtonLabels(): string[] {
  return screen.getAllByRole('button').map((b) => (b.textContent ?? '').trim()).filter(Boolean)
}

describe('SettingsModal navigation (M4 IA)', () => {
  beforeEach(() => {
    frameworkMock.values = { ...mockValues }
    frameworkMock.discovery = mockDiscovery as never
    frameworkMock.installs = {}
  })

  it('exposes all eight first-class navigation destinations', () => {
    render(<SettingsModal open onClose={vi.fn()} />)
    const labels = navButtonLabels()
    for (const label of NAV) {
      expect(labels).toContain(label)
    }
  })

  it('does not expose Advanced as a first-class destination', () => {
    render(<SettingsModal open onClose={vi.fn()} />)
    expect(navButtonLabels()).not.toContain('Advanced')
  })

  it('opens General first and renders it without experience cards', () => {
    render(<SettingsModal open onClose={vi.fn()} />)
    expect(screen.getByText('Cozmo is running')).toBeTruthy()
    expect(screen.queryByText('Experience')).toBeNull()
    expect(screen.queryByRole('button', { name: /Light|Medium|Heavy|Custom/i })).toBeNull()
  })

  it('navigates to a page and renders its content', () => {
    render(<SettingsModal open onClose={vi.fn()} />)
    fireEvent.click(screen.getAllByRole('button').find((b) => b.textContent === 'Models')!)
    expect(screen.getByText('Model library')).toBeTruthy()
  })

  it('has exactly eight first-class destinations', () => {
    render(<SettingsModal open onClose={vi.fn()} />)
    // Leave General first so its content-area quick-link buttons don't get
    // counted alongside the sidebar nav buttons.
    fireEvent.click(screen.getAllByRole('button').find((b) => b.textContent === 'Models')!)
    const nav = navButtonLabels().filter((l) => NAV.includes(l))
    expect(nav).toHaveLength(8)
    expect(new Set(nav).size).toBe(8)
  })

  it('keeps Permissions as a destination distinct from Connectors', () => {
    render(<SettingsModal open onClose={vi.fn()} />)
    const labels = navButtonLabels()
    expect(labels).toContain('Permissions')
    expect(labels).toContain('Connectors')
  })

  it('Developer is the home for internal/diagnostic settings, not capability routing', () => {
    render(<SettingsModal open onClose={vi.fn()} />)
    fireEvent.click(screen.getAllByRole('button').find((b) => b.textContent === 'Developer')!)
    expect(screen.getByText('Expert configuration')).toBeTruthy()
    expect(screen.queryByText('Internal model routing')).toBeNull()
    expect(screen.queryByText(/select capabilit/i)).toBeNull()
  })
})
