import { useState } from 'react'
import { X } from 'lucide-react'

interface Props {
  initial?: { name: string; description: string; sharedContext: string }
  onSubmit: (data: { name: string; description: string; sharedContext: string }) => void
  onCancel: () => void
}

export function ProjectForm({ initial, onSubmit, onCancel }: Props) {
  const [name, setName] = useState(initial?.name ?? '')
  const [description, setDescription] = useState(initial?.description ?? '')
  const [sharedContext, setSharedContext] = useState(initial?.sharedContext ?? '')

  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault()
    if (!name.trim()) return
    onSubmit({ name: name.trim(), description: description.trim(), sharedContext: sharedContext.trim() })
  }

  const inputClasses =
    "w-full bg-base-850 border border-transparent rounded-lg px-3 py-2 text-sm text-base-100 placeholder:text-base-500 focus:outline-none focus:border-accent/50 transition-colors"

  return (
    <form onSubmit={handleSubmit} className="space-y-3">
      <div className="flex items-center justify-between">
        <h3 className="text-sm text-base-100">{initial ? 'Edit project' : 'New project'}</h3>
        <button type="button" onClick={onCancel} className="p-1 rounded text-base-500 hover:text-base-100 transition-colors">
          <X size={14} />
        </button>
      </div>

      <input
        value={name}
        onChange={(e) => setName(e.target.value)}
        placeholder="Project name"
        className={inputClasses}
        autoFocus
      />
      <input
        value={description}
        onChange={(e) => setDescription(e.target.value)}
        placeholder="Description (optional)"
        className={inputClasses}
      />
      <textarea
        value={sharedContext}
        onChange={(e) => setSharedContext(e.target.value)}
        placeholder="Shared context — injected into every conversation in this project"
        rows={4}
        className={`${inputClasses} resize-none`}
      />

      <button
        type="submit"
        disabled={!name.trim()}
        className="w-full py-2 rounded-lg bg-accent hover:bg-accent/90 text-white text-sm font-medium disabled:opacity-40 disabled:cursor-not-allowed transition-colors"
      >
        {initial ? 'Save changes' : 'Create project'}
      </button>
    </form>
  )
}