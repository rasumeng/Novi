// Thin, defensive bridge to Tauri-only capabilities. The same React app also
// runs in a plain browser tab during development (this project's preview
// tooling opens the Vite dev server directly, outside the Tauri webview), so
// every call here must no-op cleanly rather than throw when `__TAURI_INTERNALS__`
// isn't present. Native notifications/focus state are a nice-to-have layered
// on top of the in-app toast/notification center — never a hard dependency.

let cachedHasTauri: boolean | null = null

function hasTauri(): boolean {
  if (cachedHasTauri === null) {
    cachedHasTauri = typeof window !== 'undefined' && '__TAURI_INTERNALS__' in window
  }
  return cachedHasTauri
}

export async function isWindowFocused(): Promise<boolean> {
  if (!hasTauri()) return true
  try {
    const { getCurrentWindow } = await import('@tauri-apps/api/window')
    return await getCurrentWindow().isFocused()
  } catch {
    return true
  }
}

export async function sendNativeNotification(title: string, body: string): Promise<void> {
  if (!hasTauri()) return
  try {
    const { isPermissionGranted, requestPermission, sendNotification } = await import('@tauri-apps/plugin-notification')
    let granted = await isPermissionGranted()
    if (!granted) {
      granted = (await requestPermission()) === 'granted'
    }
    if (granted) sendNotification({ title, body })
  } catch {
    // Native notifications are best-effort; failures here must never surface to the user.
  }
}

/** Notify only if it's worth interrupting the user — i.e. they're not already looking at the window. */
export async function notifyIfUnfocused(title: string, body: string): Promise<void> {
  if (await isWindowFocused()) return
  await sendNativeNotification(title, body)
}
