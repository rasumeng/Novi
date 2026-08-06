import { useState, useEffect, useRef } from 'react'
import { motion, AnimatePresence } from 'framer-motion'
import { Search, X, MessageSquareText, SearchX, WifiOff } from 'lucide-react'
import { useFocusTrap } from '@/hooks/useFocusTrap'
import { EmptyState } from '@/components/common/EmptyState'
import { LoadingSkeleton } from '@/components/common/LoadingSkeleton'

const API_BASE = import.meta.env.DEV ? 'http://localhost:8765' : ''

interface SearchResult {
  id: string
  title: string
  pinned: boolean
  mode: string
  match: string
}

interface Props {
  open: boolean
  onClose: () => void
  onSelect: (id: string) => void
}

export function SearchModal({ open, onClose, onSelect }: Props) {
  const [query, setQuery] = useState('')
  const [results, setResults] = useState<SearchResult[]>([])
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState(false)
  const inputRef = useRef<HTMLInputElement>(null)
  const modalRef = useRef<HTMLDivElement>(null)

  useFocusTrap(modalRef, open)

  useEffect(() => {
    if (!open) return
    setQuery('')
    setResults([])
    setTimeout(() => inputRef.current?.focus(), 50)
  }, [open])

  useEffect(() => {
    if (!open) return
    const handleKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') { onClose(); return }
      if (e.key === 'Enter' && results.length > 0) {
        onSelect(results[0].id)
        onClose()
      }
    }
    const handleClick = (e: MouseEvent) => {
      if (modalRef.current && !modalRef.current.contains(e.target as Node)) {
        onClose()
      }
    }
    window.addEventListener('keydown', handleKey)
    window.addEventListener('mousedown', handleClick)
    return () => {
      window.removeEventListener('keydown', handleKey)
      window.removeEventListener('mousedown', handleClick)
    }
  }, [open, onClose, onSelect, results])

  useEffect(() => {
    if (!query.trim()) { setResults([]); setError(false); return }
    setLoading(true)
    setError(false)
    const timeout = setTimeout(() => {
      fetch(`${API_BASE}/api/conversations/search?q=${encodeURIComponent(query)}`)
        .then((r) => {
          if (!r.ok) throw new Error('search request failed')
          return r.json()
        })
        .then((list) => setResults(list))
        .catch(() => {
          setResults([])
          setError(true)
        })
        .finally(() => setLoading(false))
    }, 200)
    return () => clearTimeout(timeout)
  }, [query])

  return (
    <AnimatePresence>
      {open && (
        <motion.div
          initial={{ opacity: 0 }}
          animate={{ opacity: 1 }}
          exit={{ opacity: 0 }}
          className="fixed inset-0 z-50 flex items-start justify-center pt-[15vh] bg-black/60 backdrop-blur-sm"
        >
          <motion.div
            ref={modalRef}
            role="dialog"
            aria-modal="true"
            aria-label="Search conversations"
            initial={{ opacity: 0, y: -16 }}
            animate={{ opacity: 1, y: 0 }}
            exit={{ opacity: 0, y: -16 }}
            transition={{ duration: 0.12, ease: 'easeOut' }}
            className="w-[520px] max-h-[380px] flex flex-col rounded-2xl border border-base-700 bg-base-900 shadow-panel overflow-hidden"
          >
            <div className="flex items-center gap-2 px-4 h-12 border-b border-base-800 shrink-0">
              <Search size={15} className="text-base-500 shrink-0" />
              <input
                ref={inputRef}
                value={query}
                onChange={(e) => setQuery(e.target.value)}
                placeholder="Search chats, tasks, sessions..."
                aria-label="Search conversations"
                className="flex-1 bg-transparent text-sm text-base-100 placeholder:text-base-500 outline-none"
              />
              {query && (
                <button onClick={() => setQuery('')} aria-label="Clear search" className="p-1 rounded text-base-500 hover:text-base-100">
                  <X size={14} />
                </button>
              )}
            </div>
            <div className="flex-1 overflow-y-auto">
              {loading && (
                <LoadingSkeleton rows={3} compact />
              )}
              {!loading && error && (
                <EmptyState
                  compact
                  tone="error"
                  icon={WifiOff}
                  title="Search couldn't be completed"
                  description="Check your connection and try again."
                />
              )}
              {!loading && !error && query && results.length === 0 && (
                <EmptyState
                  compact
                  icon={Search}
                  title="No matches"
                  description={`Nothing found for "${query.trim()}".`}
                />
              )}
              {results.map((r) => {
                return (
                  <button
                    key={r.id}
                    onClick={() => { onSelect(r.id); onClose() }}
                    className="w-full flex items-start gap-3 px-4 py-3 text-left hover:bg-base-800 transition-colors border-b border-base-800/50 last:border-0"
                  >
                    <MessageSquareText size={14} className="text-base-500 shrink-0 mt-1" />
                    <div className="flex-1 min-w-0">
                      <p className="text-sm text-base-100 truncate">{r.title}</p>
                      <p className="text-[11px] text-base-500 line-clamp-2 mt-0.5 leading-relaxed">
                        {r.match}
                      </p>
                    </div>
                  </button>
                )
              })}
            </div>
          </motion.div>
        </motion.div>
      )}
    </AnimatePresence>
  )
}