import { useState, useRef, useEffect } from 'react'
import { motion, AnimatePresence } from 'framer-motion'
import { X, Folder, Plus, Trash2 } from 'lucide-react'
import { Project, SourceFolder } from '@/types'
import { API_BASE } from '@/components/settings/api'
import { useFocusTrap } from '@/hooks/useFocusTrap'

interface Props {
  open: boolean
  onClose: () => void
  project: Project
  onUpdate: (id: string, data: Partial<Project>) => Promise<Project | null>
  onAddSource: (projId: string, path: string) => Promise<SourceFolder>
  onRemoveSource: (projId: string, path: string) => Promise<void>
  onDelete: (id: string) => void
}

export function ProjectSettingsModal({
  open,
  onClose,
  project,
  onUpdate,
  onAddSource,
  onRemoveSource,
  onDelete,
}: Props) {
  const modalRef = useRef<HTMLDivElement>(null)
  const [name, setName] = useState(project.name)
  const [instructions, setInstructions] = useState(project.sharedContext)
  const [sources, setSources] = useState<SourceFolder[]>(project.sources || [])
  const [workspaceError, setWorkspaceError] = useState<string | null>(null)
  const [saving, setSaving] = useState(false)

  useFocusTrap(modalRef, open)

  useEffect(() => {
    setName(project.name)
    setInstructions(project.sharedContext)
    setSources(project.sources || [])
    setWorkspaceError(null)
  }, [project])

  const pickAndAttachFolder = async () => {
    try {
      const response = await fetch(`${API_BASE}/api/directory-picker`, { method: 'POST' })
      const data = await response.json()
      if (!response.ok || !data.path) {
        if (!data.path && response.ok) return
        setWorkspaceError(data.error || 'Could not select that folder')
        return
      }
      const source = await onAddSource(project.id, data.path)
      setSources(prev => prev.some(item => item.root === source.root) ? prev : [...prev, source])
    } catch (error) {
      setWorkspaceError(error instanceof Error ? error.message : 'Could not attach')
    }
  }

  const handleRemoveSource = async (root: string) => {
    try {
      await onRemoveSource(project.id, root)
      setSources(prev => prev.filter(s => s.root !== root))
    } catch (e) {
      console.error(e)
    }
  }

  const handleSave = async () => {
    if (!name.trim()) return
    setSaving(true)
    try {
      const res = await onUpdate(project.id, { name: name.trim(), sharedContext: instructions.trim() })
      if (res !== null) onClose()
    } finally {
      setSaving(false)
    }
  }

  const handleDelete = () => {
    onClose()
    // parent handles confirm then delete (close first per spec)
    setTimeout(() => onDelete(project.id), 150)
  }

  if (!open) return null

  return (
    <AnimatePresence>
      <motion.div
        initial={{ opacity: 0 }}
        animate={{ opacity: 1 }}
        exit={{ opacity: 0 }}
        className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 backdrop-blur-sm p-4"
        onClick={onClose}
      >
        <motion.div
          ref={modalRef}
          role="dialog"
          aria-modal="true"
          aria-label="Project settings"
          initial={{ opacity: 0, y: 12, scale: 0.97 }}
          animate={{ opacity: 1, y: 0, scale: 1 }}
          exit={{ opacity: 0, y: 12, scale: 0.97 }}
          transition={{ duration: 0.14, ease: 'easeOut' }}
          className="w-[520px] max-w-full rounded-2xl border border-base-700 bg-base-900 shadow-panel p-5"
          onClick={(e) => e.stopPropagation()}
        >
          <div className="flex items-center justify-between mb-4">
            <h3 className="text-sm font-medium text-base-100">Project Settings</h3>
            <button
              onClick={onClose}
              className="p-1 rounded-lg text-base-500 hover:text-base-100 hover:bg-base-800 transition-colors"
              aria-label="Close"
            >
              <X size={16} />
            </button>
          </div>

          <div className="space-y-4">
            <div>
              <label className="block text-[11px] font-medium text-base-500 mb-1">Project Name</label>
              <input
                value={name}
                onChange={(e) => setName(e.target.value)}
                placeholder="Project name"
                className="w-full bg-base-850 border border-base-700 rounded-lg px-3 py-2 text-sm text-base-100 placeholder:text-base-500 focus:outline-none focus:border-accent/50 transition-colors"
              />
            </div>

            <div>
              <label className="block text-[11px] font-medium text-base-500 mb-1">Instructions</label>
              <textarea
                value={instructions}
                onChange={(e) => setInstructions(e.target.value)}
                placeholder="Anything I should keep in mind for every conversation here?"
                rows={4}
                className="w-full bg-base-850 border border-base-700 rounded-lg px-3 py-2 text-sm text-base-100 placeholder:text-base-500 focus:outline-none focus:border-accent/50 transition-colors resize-none"
              />
            </div>

            <div>
              <div className="flex items-center justify-between mb-2">
                <label className="text-[11px] font-medium text-base-500">Source Folders</label>
                <button
                  onClick={pickAndAttachFolder}
                  disabled={sources.length >= 3}
                  className="flex items-center gap-1.5 px-2.5 py-1 rounded-lg bg-base-850 border border-base-700 text-xs text-base-300 hover:bg-base-800 transition-colors"
                >
                  <Plus size={11} /> {sources.length >= 3 ? 'Limit reached' : 'Add Folder'}
                </button>
              </div>
              {sources.length > 0 ? (
                <div className="space-y-2">
                  {sources.map((src) => (
                    <div key={src.hash || src.root} className="flex items-center gap-2 px-3 py-2 rounded-lg bg-base-950/50 border border-base-800/30 group">
                      <Folder size={12} className="text-accent shrink-0" />
                      <span className="truncate flex-1 font-mono text-[12px] text-base-300">{src.root}</span>
                      <button
                        onClick={() => handleRemoveSource(src.root)}
                        className="opacity-0 group-hover:opacity-100 p-1 rounded text-base-500 hover:text-err"
                        aria-label={`Remove source folder ${src.root}`}
                      >
                        <X size={11} />
                      </button>
                    </div>
                  ))}
                  <p className="text-[11px] text-base-600">I can list, search, and read files here — skipping .git, node_modules, venv, build.</p>
                </div>
              ) : (
                <p className="text-[11px] text-base-600">No source folders attached yet.</p>
              )}
              {workspaceError && <p className="text-xs text-err mt-2">{workspaceError}</p>}
            </div>
          </div>

          <div className="flex items-center justify-between mt-6 pt-4 border-t border-base-800">
            <button
              onClick={handleDelete}
              className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-sm text-err hover:bg-base-800 transition-colors"
            >
              <Trash2 size={13} /> Delete
            </button>
            <div className="flex items-center gap-2">
              <button
                onClick={onClose}
                className="px-3 py-1.5 rounded-lg bg-base-800 text-sm text-base-300 hover:bg-base-700 transition-colors"
              >
                Cancel
              </button>
              <button
                onClick={handleSave}
                disabled={saving || !name.trim()}
                className="px-3 py-1.5 rounded-lg bg-accent text-white text-sm font-medium hover:bg-accent/90 disabled:opacity-40 disabled:cursor-not-allowed transition-colors"
              >
                {saving ? 'Saving…' : 'Save'}
              </button>
            </div>
          </div>
        </motion.div>
      </motion.div>
    </AnimatePresence>
  )
}
