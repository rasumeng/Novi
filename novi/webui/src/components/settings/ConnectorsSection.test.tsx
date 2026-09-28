import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { WebSearchCard } from './ConnectorsSection'

afterEach(() => {
  vi.restoreAllMocks()
})

function framework(values: Record<string, unknown> = {}) {
  return {
    values: {
      'search.backend': '',
      'search.brave_api_key': '',
      'search.url': 'http://localhost:8080',
      ...values,
    },
    set: vi.fn().mockResolvedValue(true),
    reload: vi.fn().mockResolvedValue(undefined),
  } as any
}

describe('WebSearchCard SearXNG setup', () => {
  it('offers one-click setup when web search is not configured', () => {
    render(<WebSearchCard framework={framework()} />)

    expect(screen.getByRole('button', { name: /enable with searxng/i })).toBeTruthy()
  })

  it('runs setup and refreshes persisted settings after success', async () => {
    const settings = framework()
    vi.spyOn(globalThis, 'fetch').mockResolvedValue({
      ok: true,
      json: async () => ({ ok: true, state: 'connected', message: 'SearXNG is ready.' }),
    } as Response)
    render(<WebSearchCard framework={settings} />)

    fireEvent.click(screen.getByRole('button', { name: /enable with searxng/i }))

    await waitFor(() => expect(globalThis.fetch).toHaveBeenCalledWith(
      expect.stringContaining('/api/search/setup/searxng'),
      { method: 'POST' },
    ))
    await waitFor(() => expect(settings.reload).toHaveBeenCalled())
    expect(await screen.findByText('SearXNG is ready.')).toBeTruthy()
  })
})
