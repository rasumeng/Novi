import { motion } from 'framer-motion'
import { ArrowUpCircle } from 'lucide-react'

interface Props {
  version: string
  installing: boolean
  onInstall: () => void
  onDismiss: () => void
}

/**
 * Non-blocking update prompt. Rendered by App once `useUpdateCheck` reports a
 * newer release. Deliberately a corner banner rather than a modal: an update
 * must never interrupt work in progress, and the app keeps running normally
 * while it is visible.
 */
export function UpdatePrompt({ version, installing, onInstall, onDismiss }: Props) {
  return (
    <motion.div
      role="status"
      initial={{ opacity: 0, y: 8 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ duration: 0.15 }}
      className="fixed bottom-4 right-4 z-[70] w-[320px] rounded-2xl border border-base-700 bg-base-900 p-4 shadow-panel"
    >
      <div className="flex items-center gap-2.5 mb-2">
        <div className="w-8 h-8 rounded-lg flex items-center justify-center bg-accent/15">
          <ArrowUpCircle size={16} className="text-accent" />
        </div>
        <p className="text-sm font-medium text-base-100">Novi {version} is available</p>
      </div>
      <p className="text-xs text-base-400 mb-3 leading-relaxed">
        Install it now and Novi will restart. Your conversations are not affected.
      </p>
      <div className="flex justify-end gap-2">
        <button
          onClick={onDismiss}
          disabled={installing}
          className="px-3.5 py-1.5 rounded-lg text-sm text-base-300 hover:bg-base-800 transition-colors disabled:opacity-50"
        >
          Later
        </button>
        <button
          onClick={onInstall}
          disabled={installing}
          className="px-3.5 py-1.5 rounded-lg text-sm text-white bg-accent hover:bg-accent/90 transition-colors disabled:opacity-50"
        >
          {installing ? 'Installing…' : 'Install & restart'}
        </button>
      </div>
    </motion.div>
  )
}