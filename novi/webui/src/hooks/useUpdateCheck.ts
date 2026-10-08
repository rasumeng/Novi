import { useCallback, useEffect, useRef, useState } from 'react'
import { isTauri } from '@tauri-apps/api/core'
import { check, type Update } from '@tauri-apps/plugin-updater'
import { relaunch } from '@tauri-apps/plugin-process'

const LAST_CHECK_KEY = 'novi_update_last_check'
const CHECK_INTERVAL_MS = 24 * 60 * 60 * 1000

export interface UpdateCheckState {
  /** Non-null once a newer version has been announced by the update endpoint. */
  update: Update | null
  /** True while the update is being downloaded and installed. */
  installing: boolean
  /** User dismissed the prompt for this session. */
  dismissed: boolean
  /** Download + install the pending update, then relaunch. Never throws. */
  install: () => Promise<void>
  dismiss: () => void
}

function readLastCheck(): number {
  try {
    const raw = localStorage.getItem(LAST_CHECK_KEY)
    return raw ? Number(raw) || 0 : 0
  } catch {
    return 0
  }
}

function writeLastCheck(at: number): void {
  try {
    localStorage.setItem(LAST_CHECK_KEY, String(at))
  } catch {
    /* private mode / disabled storage: throttle silently degrades to per-launch */
  }
}

/**
 * Best-effort, at-most-once-a-day check for a newer Novi release.
 *
 * This is advisory background work and is allowed to fail in every direction:
 * offline, a 404 on the update endpoint, or running in a plain browser via
 * `npm run dev` (no Tauri IPC) all resolve to "no update, no error". Nothing
 * here may block or break launch, so every await is inside a try/catch and no
 * state is set on the failure path.
 *
 * The 24h throttle is keyed off localStorage, matching the other cosmetic
 * keys in this app (`novi_active_project_id` et al). The timestamp is written
 * *before* the request is issued so a failing endpoint cannot cause a check on
 * every launch.
 */
export function useUpdateCheck(): UpdateCheckState {
  const [update, setUpdate] = useState<Update | null>(null)
  const [installing, setInstalling] = useState(false)
  const [dismissed, setDismissed] = useState(false)
  const startedRef = useRef(false)

  useEffect(() => {
    // Guard against a second pass in StrictMode's double-invoked effects, so
    // two concurrent `check()` calls cannot race on the same promise.
    if (startedRef.current) return
    if (!isTauri()) return
    if (Date.now() - readLastCheck() < CHECK_INTERVAL_MS) return
    startedRef.current = true
    writeLastCheck(Date.now())

    void (async () => {
      try {
        const found = await check()
        if (found) setUpdate(found)
      } catch {
        /* offline, endpoint down, no manifest yet - stay silent */
      }
    })()
  }, [])

  const install = useCallback(async () => {
    if (!update || installing) return
    setInstalling(true)
    try {
      await update.downloadAndInstall()
      await relaunch()
    } catch {
      // A failed download must leave the app usable and running; the user can
      // retry from this same prompt.
      setInstalling(false)
    }
  }, [update, installing])

  const dismiss = useCallback(() => setDismissed(true), [])

  return { update, installing, dismissed, install, dismiss }
}