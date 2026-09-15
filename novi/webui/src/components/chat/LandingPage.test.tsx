import { act, cleanup, render, screen } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { LandingPage } from './LandingPage'
import { fetchSystemHealth } from '@/components/settings/api'
vi.mock('@/services/novi', () => ({ fetchKnowledgeOverview: vi.fn().mockResolvedValue(null) }))
vi.mock('@/components/settings/api', () => ({ fetchSystemHealth: vi.fn() }))
vi.mock('@/components/brand/NoviMascot', () => ({ NoviMascot: () => null }))
afterEach(() => { cleanup(); vi.mocked(fetchSystemHealth).mockReset() })
describe('setup readiness', () => {
  it('clears stale setup warning after model configuration changes', async () => {
    vi.mocked(fetchSystemHealth).mockResolvedValueOnce({ ready: false } as any)
      .mockResolvedValueOnce({ ready: true } as any)
    await act(async () => { render(<LandingPage />) })
    expect(screen.queryByText('Finish setting up Novi')).not.toBeNull()
    await act(async () => { window.dispatchEvent(new Event('novi:readiness-changed')) })
    expect(screen.queryByText('Finish setting up Novi')).toBeNull()
  })
  it('keeps the warning if setup is still incomplete', async () => {
    vi.mocked(fetchSystemHealth).mockResolvedValue({ ready: false } as any)
    await act(async () => { render(<LandingPage />) })
    await act(async () => { window.dispatchEvent(new Event('novi:readiness-changed')) })
    expect(screen.queryByText('Finish setting up Novi')).not.toBeNull()
  })
})
