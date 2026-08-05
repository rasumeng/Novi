import { useState, useRef, useEffect } from 'react'
import clsx from 'clsx'
import { Pin, PinOff, MoreHorizontal, Pencil, Trash2 } from 'lucide-react'
import { Conversation } from '@/types'
import { useConfirm } from '@/hooks/useConfirm'

export function SidebarItem({
  conversation,
  active,
  onClick,
  onPin,
  onRename,
  onDelete,
}: {
  conversation: Conversation
  active: boolean
  onClick: () => void
  onPin: (id: string) => void
  onRename: (id: string, title: string) => void
  onDelete: (id: string) => void
}) {
  const { confirm, dialog } = useConfirm()
  const [menuOpen, setMenuOpen] = useState(false)
  const [renaming, setRenaming] = useState(false)
  const [renameValue, setRenameValue] = useState(conversation.title)
  const menuRef = useRef<HTMLDivElement>(null)
  const inputRef = useRef<HTMLInputElement>(null)

  useEffect(() => {
    if (renaming) inputRef.current?.focus()
  }, [renaming])

  useEffect(() => {
    if (!menuOpen) return
    const close = (e: MouseEvent) => {
      if (menuRef.current && !menuRef.current.contains(e.target as Node))
        setMenuOpen(false)
    }
    document.addEventListener('mousedown', close)
    return () => document.removeEventListener('mousedown', close)
  }, [menuOpen])

  const submitRename = () => {
    const trimmed = renameValue.trim()
    if (trimmed) onRename(conversation.id, trimmed)
    setRenaming(false)
  }

  const handleDelete = async () => {
    const ok = await confirm({
      title: 'Delete this conversation?',
      description: `"${conversation.title}" will be permanently deleted. This can't be undone.`,
      confirmLabel: 'Delete',
    })
    if (ok) onDelete(conversation.id)
  }

  return (
    <div className="relative group">
      {dialog}
      <button
        onClick={onClick}
        className={clsx(
          'w-full text-left px-2.5 py-2 rounded-xl text-sm flex items-center justify-between gap-1 transition-colors',
          active ? 'bg-base-800 text-base-100' : 'text-base-300 hover:bg-base-850 hover:text-base-100'
        )}
      >
        <div className="flex items-center gap-1.5 min-w-0 flex-1">
          <button
            onClick={(e) => { e.stopPropagation(); onPin(conversation.id) }}
            className={clsx(
              'shrink-0 transition-opacity',
              conversation.pinned ? 'opacity-100' : 'opacity-0 group-hover:opacity-100'
            )}
            title={conversation.pinned ? 'Unpin' : 'Pin'}
          >
            {conversation.pinned ? (
              <Pin size={12} className="text-accent" />
            ) : (
              <Pin size={12} className="text-base-400 hover:text-accent" />
            )}
          </button>
          {renaming ? (
            <input
              ref={inputRef}
              value={renameValue}
              onChange={(e) => setRenameValue(e.target.value)}
              onBlur={submitRename}
              onKeyDown={(e) => { if (e.key === 'Enter') submitRename(); if (e.key === 'Escape') setRenaming(false) }}
              className="flex-1 bg-transparent border-b border-accent text-base-100 text-sm outline-none min-w-0"
              onClick={(e) => e.stopPropagation()}
            />
          ) : (
            <span className="truncate">{conversation.title}</span>
          )}
        </div>
        {!renaming && (
          <button
            onClick={(e) => { e.stopPropagation(); setMenuOpen(!menuOpen) }}
            className="shrink-0 opacity-0 group-hover:opacity-100 transition-opacity p-0.5 rounded text-base-400 hover:text-base-100"
          >
            <MoreHorizontal size={14} />
          </button>
        )}
      </button>

      {menuOpen && (
        <div
          ref={menuRef}
          className="absolute right-0 top-full mt-1 w-36 rounded-xl border border-base-700 bg-base-850 shadow-lg z-50 py-1"
        >
          <button
            onClick={(e) => { e.stopPropagation(); onPin(conversation.id); setMenuOpen(false) }}
            className="w-full flex items-center gap-2 px-3 py-1.5 text-sm text-base-300 hover:bg-base-750 hover:text-base-100"
          >
            {conversation.pinned ? <PinOff size={14} /> : <Pin size={14} />}
            {conversation.pinned ? 'Unpin' : 'Pin'}
          </button>
          <button
            onClick={(e) => { e.stopPropagation(); setRenaming(true); setRenameValue(conversation.title); setMenuOpen(false) }}
            className="w-full flex items-center gap-2 px-3 py-1.5 text-sm text-base-300 hover:bg-base-800 hover:text-base-100"
          >
            <Pencil size={14} />
            Rename
          </button>
          <div className="border-t border-base-700 my-1" />
          <button
            onClick={(e) => { e.stopPropagation(); setMenuOpen(false); handleDelete() }}
            className="w-full flex items-center gap-2 px-3 py-1.5 text-sm text-err hover:bg-base-800"
          >
            <Trash2 size={14} />
            Delete
          </button>
        </div>
      )}
    </div>
  )
}