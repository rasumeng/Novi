import { useEffect, useRef } from 'react'
import { motion } from 'framer-motion'

export function ThinkingTrace({ text }: { text: string }) {
  const scrollRef = useRef<HTMLDivElement>(null)

  // Keep the newest reasoning visible as it streams in.
  useEffect(() => {
    const el = scrollRef.current
    if (el) el.scrollTop = el.scrollHeight
  }, [text])

  return (
    <motion.div
      initial={{ opacity: 0, y: 4 }}
      animate={{ opacity: 1, y: 0 }}
      className="rounded-xl border border-base-700/50 bg-base-900/50 overflow-hidden"
    >
      <div className="flex items-center gap-2 px-3 py-2 border-b border-base-800/50">
        <span className="flex gap-1">
          <span className="w-1 h-1 rounded-full bg-accent animate-glow" />
          <span className="w-1 h-1 rounded-full bg-accent animate-glow" style={{ animationDelay: '0.2s' }} />
          <span className="w-1 h-1 rounded-full bg-accent animate-glow" style={{ animationDelay: '0.4s' }} />
        </span>
        <span className="text-[12px] font-medium text-base-200">Thinking...</span>
      </div>
      <div ref={scrollRef} className="px-3 py-2 max-h-56 overflow-y-auto">
        <p className="whitespace-pre-wrap text-[12.5px] leading-relaxed text-base-400 font-sans">
          {text}
        </p>
      </div>
    </motion.div>
  )
}
