import { useEffect, type RefObject } from 'react'

// Traps keyboard focus inside a dialog/popover so Tab/Shift+Tab don't escape.
// Minimal and dependency-free; Escape is left to each surface (it may need to
// save state first, e.g. SettingsModal).
export function useFocusTrap(ref: RefObject<HTMLElement | null>, active: boolean) {
  useEffect(() => {
    if (!active || !ref.current) return
    const root = ref.current
    const handleKey = (e: KeyboardEvent) => {
      if (e.key !== 'Tab') return
      const focusables = root.querySelectorAll<HTMLElement>(
        'button, [href], input, select, textarea, [tabindex]:not([tabindex="-1"])'
      )
      if (focusables.length === 0) return
      const first = focusables[0]
      const last = focusables[focusables.length - 1]
      const current = document.activeElement
      if (e.shiftKey && (current === first || !root.contains(current))) {
        e.preventDefault()
        last.focus()
      } else if (!e.shiftKey && (current === last || !root.contains(current))) {
        e.preventDefault()
        first.focus()
      }
    }
    document.addEventListener('keydown', handleKey)
    return () => document.removeEventListener('keydown', handleKey)
  }, [ref, active])
}