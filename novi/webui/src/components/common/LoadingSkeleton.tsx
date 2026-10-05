// Shared loading placeholder. Motion lives in globals.css so every async
// surface uses the same timing and reduced-motion behavior.

interface Props {
  rows?: number
  /** Render as a single constrained block (e.g. inside a modal body). */
  compact?: boolean
  className?: string
}

export function LoadingSkeleton({ rows = 3, compact = false, className = '' }: Props) {
  return (
    <div className={`flex flex-col gap-3 ${compact ? '' : 'p-4'} ${className}`} aria-hidden="true">
      {Array.from({ length: rows }).map((_, i) => (
        <div
          key={i}
          className="novi-loading-skeleton w-full h-9 rounded-xl"
          style={{ animationDelay: `${i * 140}ms` }}
        />
      ))}
    </div>
  )
}
