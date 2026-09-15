import { motion } from 'framer-motion'
import { FileText } from 'lucide-react'
import { ChatMessage } from '@/types'

function formatFileSize(bytes: number): string {
  if (bytes < 1024) return bytes + ' B'
  if (bytes < 1024 * 1024) return (bytes / 1024).toFixed(1) + ' KB'
  return (bytes / (1024 * 1024)).toFixed(1) + ' MB'
}

export function UserMessage({ message }: { message: ChatMessage }) {
  return (
    <motion.div
      initial={{ opacity: 0, y: 4 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ duration: 0.2 }}
      className="flex flex-col items-end gap-1.5"
      data-testid="user-message"
    >
      <div
        className="rounded-2xl px-4 py-3 text-[15px] leading-relaxed max-w-[75%] overflow-hidden bg-accent/20 text-white selection:bg-white/40 selection:text-white break-words"
      >
        <p className="whitespace-pre-wrap break-words leading-relaxed overflow-wrap-anywhere">
          {message.content}
        </p>
        {message.attachments?.map(att => (
          <div key={att.id} className="mt-2 first:mt-0">
            {att.type === 'image' ? (
              <a href={att.url} target="_blank" rel="noreferrer">
                <img
                  src={att.thumbnail || att.url}
                  alt={att.name}
                  className="max-w-xs rounded-lg border border-white/20 cursor-pointer hover:opacity-90 transition-opacity"
                />
              </a>
            ) : (
              <a
                href={att.url}
                download={att.name}
                className="flex items-center gap-2 px-3 py-2 rounded-lg bg-white/15 text-white hover:bg-white/20 transition-colors text-sm"
              >
                <FileText size={14} />
                <span className="truncate max-w-[200px]">{att.name}</span>
                <span className="text-white/70">{formatFileSize(att.size)}</span>
              </a>
            )}
          </div>
        ))}
      </div>
      <span className="text-[11px] text-base-500 px-1 text-right">{message.createdAt}</span>
    </motion.div>
  )
}
