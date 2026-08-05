import { useEffect, useRef } from 'react'
import { motion } from 'framer-motion'
import { AlertTriangle } from 'lucide-react'

export interface ConfirmRequest {
  title: string
  description: string
  confirmLabel?: string
  cancelLabel?: string
  danger?: boolean
}

interface Props {
  request: ConfirmRequest
  onConfirm: () => void
  onCancel: () => void
}

// Generic confirmation dialog for destructive actions (delete memory item,
// remove a connector, delete a skill, ...). Every caller gets the same
// accessible behavior (focus, Escape-to-cancel) instead of each settings
// section wiring its own ad hoc window.confirm() or, worse, nothing at all.
export function ConfirmDialog({ request, onConfirm, onCancel }: Props) {
  const cancelRef = useRef<HTMLButtonElement>(null)

  useEffect(() => {
    cancelRef.current?.focus()
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') onCancel()
    }
    document.addEventListener('keydown', onKey)
    return () => document.removeEventListener('keydown', onKey)
  }, [onCancel])

  return (
    <div
      className="fixed inset-0 z-[60] flex items-center justify-center bg-black/50 backdrop-blur-sm"
      onMouseDown={(e) => { if (e.target === e.currentTarget) onCancel() }}
    >
      <motion.div
        role="alertdialog"
        aria-modal="true"
        aria-labelledby="confirm-dialog-title"
        aria-describedby="confirm-dialog-description"
        initial={{ opacity: 0, scale: 0.96 }}
        animate={{ opacity: 1, scale: 1 }}
        transition={{ duration: 0.15 }}
        className="w-[380px] rounded-2xl border border-base-700 bg-base-900 p-5 shadow-panel"
      >
        <div className="flex items-center gap-2.5 mb-3">
          <div className={`w-8 h-8 rounded-lg flex items-center justify-center ${request.danger ?? true ? 'bg-err/15' : 'bg-accent/15'}`}>
            <AlertTriangle size={16} className={request.danger ?? true ? 'text-err' : 'text-accent'} />
          </div>
          <p id="confirm-dialog-title" className="text-sm font-medium text-base-100">{request.title}</p>
        </div>
        <p id="confirm-dialog-description" className="text-xs text-base-400 mb-4 leading-relaxed">
          {request.description}
        </p>
        <div className="flex justify-end gap-2">
          <button
            ref={cancelRef}
            onClick={onCancel}
            className="px-3.5 py-1.5 rounded-lg text-sm text-base-300 hover:bg-base-800 transition-colors"
          >
            {request.cancelLabel ?? 'Cancel'}
          </button>
          <button
            onClick={onConfirm}
            className={`px-3.5 py-1.5 rounded-lg text-sm text-white transition-colors ${
              (request.danger ?? true) ? 'bg-err hover:bg-err/90' : 'bg-accent hover:bg-accent/90'
            }`}
          >
            {request.confirmLabel ?? 'Delete'}
          </button>
        </div>
      </motion.div>
    </div>
  )
}
