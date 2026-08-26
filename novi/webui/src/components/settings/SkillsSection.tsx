import { useState, useEffect, useRef } from 'react'
import { Plus, Upload, Edit3, Sparkles, Puzzle, Trash2 } from 'lucide-react'
import { uploadSkill, createSkill, deleteSkill } from '@/services/novi'
import type { Skill } from '@/types'
import { useToast } from '@/hooks/useToast'
import { useConfirm } from '@/hooks/useConfirm'

interface Props {
  skills: Skill[]
  onRefresh: () => void
  onCreateSkill?: () => void
  onClose: () => void
}

export function SkillsSection({ skills, onRefresh, onCreateSkill, onClose }: Props) {
  const { showError } = useToast()
  const { confirm, dialog } = useConfirm()
  const [menuOpen, setMenuOpen] = useState(false)
  const [writeOpen, setWriteOpen] = useState(false)
  const [writeName, setWriteName] = useState('')
  const [writeDesc, setWriteDesc] = useState('')
  const [writeContent, setWriteContent] = useState('')
  const [saving, setSaving] = useState(false)
  const fileInputRef = useRef<HTMLInputElement>(null)
  const menuRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    if (!menuOpen) return
    const close = (e: MouseEvent) => {
      if (menuRef.current && !menuRef.current.contains(e.target as Node))
        setMenuOpen(false)
    }
    document.addEventListener('mousedown', close)
    return () => document.removeEventListener('mousedown', close)
  }, [menuOpen])

  const handleUpload = async (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0]
    if (!file) return
    const result = await uploadSkill(file)
    if (!result) showError(`Couldn't upload "${file.name}" as a skill.`)
    onRefresh()
    setMenuOpen(false)
  }

  const handleWriteSubmit = async () => {
    if (!writeName.trim()) return
    setSaving(true)
    const result = await createSkill({ name: writeName.trim(), description: writeDesc.trim(), content: writeContent })
    setSaving(false)
    if (!result) {
      showError("Couldn't save this skill.")
      return
    }
    setWriteOpen(false)
    setWriteName('')
    setWriteDesc('')
    setWriteContent('')
    onRefresh()
  }

  const handleDelete = async (name: string) => {
    const ok = await confirm({
      title: `Delete "${name}"?`,
      description: "Novi won't be able to use this skill anymore. This can't be undone.",
      confirmLabel: 'Delete',
    })
    if (!ok) return
    try {
      await deleteSkill(name)
      onRefresh()
    } catch {
      showError(`Couldn't delete "${name}".`)
    }
  }

  const handleCreateWithNovi = () => {
    setMenuOpen(false)
    onClose()
    onCreateSkill?.()
  }

  return (
    <div className="space-y-4">
      {dialog}
      <div className="flex items-center justify-between">
        <p className="text-xs text-base-500">Skills extend Novi with reusable instructions.</p>
        <div className="relative" ref={menuRef}>
          <button
            onClick={() => setMenuOpen(v => !v)}
            className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg bg-accent hover:bg-accent/90 text-white text-xs font-medium transition-colors"
          >
            <Plus size={14} /> Add
          </button>
          {menuOpen && (
            <div className="absolute right-0 top-full mt-1 bg-base-850 border border-base-700 rounded-xl overflow-hidden shadow-panel min-w-[200px] z-20 py-1">
              <button
                onClick={() => { fileInputRef.current?.click() }}
                className="w-full flex items-center gap-2.5 px-3 py-2.5 text-xs text-base-200 hover:bg-base-800 transition-colors"
              >
                <Upload size={14} /> Upload .md file
              </button>
              <button
                onClick={() => { setMenuOpen(false); setWriteOpen(true) }}
                className="w-full flex items-center gap-2.5 px-3 py-2.5 text-xs text-base-200 hover:bg-base-800 transition-colors"
              >
                <Edit3 size={14} /> Write instructions
              </button>
              <div className="border-t border-base-700 my-1" />
              <button
                onClick={handleCreateWithNovi}
                className="w-full flex items-center gap-2.5 px-3 py-2.5 text-xs text-accent hover:bg-base-800 transition-colors"
              >
                <Sparkles size={14} /> Create with Novi
              </button>
            </div>
          )}
          <input
            ref={fileInputRef}
            type="file"
            accept=".md,.skill"
            className="hidden"
            onChange={handleUpload}
          />
        </div>
      </div>

      {writeOpen && (
        <div className="p-4 rounded-xl border border-base-700 bg-base-900 space-y-3">
          <input
            value={writeName}
            onChange={(e) => setWriteName(e.target.value)}
            placeholder="Skill name"
            className="w-full bg-base-800 border border-base-700 rounded-lg px-3 py-2 text-xs text-base-200 placeholder:text-base-500 outline-none focus:border-accent/40"
          />
          <input
            value={writeDesc}
            onChange={(e) => setWriteDesc(e.target.value)}
            placeholder="Short description (optional)"
            className="w-full bg-base-800 border border-base-700 rounded-lg px-3 py-2 text-xs text-base-200 placeholder:text-base-500 outline-none focus:border-accent/40"
          />
          <textarea
            value={writeContent}
            onChange={(e) => setWriteContent(e.target.value)}
            placeholder="Skill instructions in Markdown..."
            rows={8}
            className="w-full bg-base-800 border border-base-700 rounded-lg px-3 py-2 text-xs text-base-200 placeholder:text-base-500 outline-none focus:border-accent/40 resize-y font-mono"
          />
          <div className="flex items-center gap-2 justify-end">
            <button
              onClick={() => setWriteOpen(false)}
              className="px-3 py-1.5 rounded-lg text-xs text-base-400 hover:text-base-200 transition-colors"
            >
              Cancel
            </button>
            <button
              onClick={handleWriteSubmit}
              disabled={saving || !writeName.trim()}
              className="px-3 py-1.5 rounded-lg text-xs font-medium bg-accent text-white hover:bg-accent/90 disabled:opacity-50 transition-colors"
            >
              {saving ? 'Saving...' : 'Save skill'}
            </button>
          </div>
        </div>
      )}

      {skills.length === 0 ? (
        <div className="flex items-center justify-center h-32 rounded-xl border-2 border-dashed border-base-700 text-base-500 text-sm">
          No skills installed yet
        </div>
      ) : (
        <div className="space-y-2">
          {skills.map((s) => (
            <div key={s.name} className="flex items-center gap-3 px-4 py-3 rounded-xl bg-base-800/50 border border-base-700">
              <Puzzle size={16} className="text-base-400 shrink-0" />
              <div className="flex-1 min-w-0">
                <p className="text-sm text-base-100 font-medium">{s.name}</p>
                {s.description && (
                  <p className="text-xs text-base-500 truncate">{s.description}</p>
                )}
              </div>
              {s.name !== 'skill-creator' && (
                <button
                  onClick={() => handleDelete(s.name)}
                  className="p-1.5 rounded-lg text-base-400 hover:text-err hover:bg-base-800 transition-colors"
                  title="Delete skill"
                >
                  <Trash2 size={14} />
                </button>
              )}
            </div>
          ))}
        </div>
      )}
    </div>
  )
}
