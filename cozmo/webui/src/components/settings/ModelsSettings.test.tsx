import { describe, it, expect, vi } from 'vitest'
import { render, screen, fireEvent } from '@testing-library/react'
import { ModelsSettings } from './ModelsSettings'
import { mergeModelCatalog, profileToConfigPatch } from '@/product/configLayer'
import type { SettingsData } from './types'

const BASE_CONFIG: SettingsData = { models: {}, runtime: { lightweight_mode: false }, llm: { roles: {} } }
const BALANCED_CONFIG: SettingsData = { ...BASE_CONFIG, ...profileToConfigPatch('balanced', BASE_CONFIG) }
const CATALOG = mergeModelCatalog([])

describe('ModelsSettings', () => {
  it('shows the four product-concept pickers with the balanced preset selections, and keeps routing roles collapsed by default', () => {
    render(
      <ModelsSettings
        config={BALANCED_CONFIG}
        catalog={CATALOG}
        availableModels={[]}
        setConfig={vi.fn()}
        setDirty={vi.fn()}
      />
    )

    expect(screen.getByText('Conversation Model')).toBeTruthy()
    expect(screen.getByText('Coding Model')).toBeTruthy()
    expect(screen.getByText('Vision Model')).toBeTruthy()
    expect(screen.getByText('Embedding Model')).toBeTruthy()

    // Balanced assigns Llama 3.1 8B to chat — the current-selection card should show it.
    expect(screen.getByText('Llama 3.1 8B')).toBeTruthy()
    expect(screen.getByText('Qwen 2.5 Coder 7B')).toBeTruthy()

    // Backend role names must not appear until Expert Configuration is explicitly expanded.
    expect(screen.queryByText('Classifier')).toBeNull()
    expect(screen.queryByText('Orchestrator')).toBeNull()
    expect(screen.getByText('Expert Configuration')).toBeTruthy()

    fireEvent.click(screen.getByText('Expert Configuration'))
    expect(screen.getByText('Classifier')).toBeTruthy()
    expect(screen.getByText('Orchestrator')).toBeTruthy()
  })

  it('lets a beginner change the Conversation Model without ever seeing a role name', () => {
    const setConfig = vi.fn()
    render(
      <ModelsSettings
        config={BALANCED_CONFIG}
        catalog={CATALOG}
        availableModels={[]}
        setConfig={setConfig}
        setDirty={vi.fn()}
      />
    )

    const changeButtons = screen.getAllByText('Change')
    fireEvent.click(changeButtons[0]) // Conversation Model's "Change"

    // High Quality's conversation model should be selectable from the picker.
    const options = screen.getAllByText('Llama 3.1 70B')
    fireEvent.click(options[options.length - 1])

    expect(setConfig).toHaveBeenCalled()
    const patchArg = setConfig.mock.calls[0][0] as SettingsData
    expect(patchArg.llm?.roles?.chat).toMatchObject({ model: 'llama3.1:70b' })
  })

  it('marks the embedding model as unrelated to llm.roles', () => {
    const setConfig = vi.fn()
    render(
      <ModelsSettings
        config={BALANCED_CONFIG}
        catalog={CATALOG}
        availableModels={[]}
        setConfig={setConfig}
        setDirty={vi.fn()}
      />
    )
    expect(screen.getByText('No model chosen yet.')).toBeTruthy() // embedding model not set in BALANCED_CONFIG
  })
})
