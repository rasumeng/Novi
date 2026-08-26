import { createContext, useCallback, useContext, useMemo, useState, type ReactNode } from 'react'
import { AnimatePresence, motion } from 'framer-motion'
import { AlertCircle, CheckCircle2, Info, X } from 'lucide-react'

interface Toast {
  id: string
  kind: 'error' | 'success' | 'info'
  message: string
}

interface ToastContextValue {
  showError: (message: string) => void
  showSuccess: (message: string) => void
  showInfo: (message: string) => void
}

const ToastContext = createContext<ToastContextValue | null>(null)

let idCounter = 0

const ICONS = {
  error: AlertCircle,
  success: CheckCircle2,
  info: Info,
}

const STYLES = {
  error: { box: 'bg-err/10 border-err/30', icon: 'text-err', role: 'alert' as const },
  success: { box: 'bg-ok/10 border-ok/30', icon: 'text-ok', role: 'status' as const },
  info: { box: 'bg-base-850 border-base-700', icon: 'text-base-400', role: 'status' as const },
}

// App-wide, non-blocking notifications. This replaces the `.catch(() => {})`
// pattern found throughout Settings/Projects/Memory/Connectors/Skills, where
// a failed save or delete previously vanished with no visible feedback.
export function ToastProvider({ children }: { children: ReactNode }) {
  const [toasts, setToasts] = useState<Toast[]>([])

  const dismiss = useCallback((id: string) => {
    setToasts((prev) => prev.filter((t) => t.id !== id))
  }, [])

  const push = useCallback((kind: Toast['kind'], message: string) => {
    const id = `toast-${Date.now()}-${idCounter++}`
    setToasts((prev) => [...prev, { id, kind, message }])
    window.setTimeout(() => dismiss(id), kind === 'error' ? 7000 : 4000)
  }, [dismiss])

  // Stable identity: consumers (e.g. useNoviChat's effects) depend on these
  // functions, so they must not change reference every time a toast appears/disappears.
  const value = useMemo<ToastContextValue>(() => ({
    showError: (m) => push('error', m),
    showSuccess: (m) => push('success', m),
    showInfo: (m) => push('info', m),
  }), [push])

  return (
    <ToastContext.Provider value={value}>
      {children}
      <div className="fixed bottom-4 right-4 z-[100] flex flex-col gap-2 w-[320px] pointer-events-none">
        <AnimatePresence>
          {toasts.map((t) => {
            const Icon = ICONS[t.kind]
            const style = STYLES[t.kind]
            return (
              <motion.div
                key={t.id}
                initial={{ opacity: 0, y: 8, scale: 0.98 }}
                animate={{ opacity: 1, y: 0, scale: 1 }}
                exit={{ opacity: 0, y: 8, scale: 0.98 }}
                transition={{ duration: 0.15 }}
                role={style.role}
                className={`pointer-events-auto flex items-start gap-2.5 rounded-xl border p-3 shadow-panel text-xs text-base-100 ${style.box}`}
              >
                <Icon size={15} className={`shrink-0 mt-0.5 ${style.icon}`} />
                <p className="flex-1 leading-relaxed">{t.message}</p>
                <button
                  onClick={() => dismiss(t.id)}
                  aria-label="Dismiss notification"
                  className="text-base-500 hover:text-base-200 shrink-0"
                >
                  <X size={13} />
                </button>
              </motion.div>
            )
          })}
        </AnimatePresence>
      </div>
    </ToastContext.Provider>
  )
}

export function useToast() {
  const ctx = useContext(ToastContext)
  if (!ctx) throw new Error('useToast must be used within a ToastProvider')
  return ctx
}
