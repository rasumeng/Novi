// Consistent empty-state presentation. Used across Search, Activity,
// Notifications, Jobs, Projects so no surface shows a blank screen or a raw
// "No items found" string. Language and spacing are the same everywhere.

interface Props {
  icon?: React.ElementType
  title: string
  description?: string
  action?: React.ReactNode
  /** Compact variant for inline/panel contexts (smaller padding + icon). */
  compact?: boolean
  /** Error styling (e.g. a failed load), distinct from a benign empty state. */
  tone?: 'default' | 'error'
}

export function EmptyState({ icon: Icon, title, description, action, compact = false, tone = 'default' }: Props) {
  return (
    <div className={`flex flex-col items-center justify-center text-center ${compact ? 'px-4 py-8' : 'px-6 py-16'}`}>
      {Icon && (
        <div
          className={`flex items-center justify-center ${compact ? 'w-9 h-9 rounded-xl mb-2.5' : 'w-12 h-12 rounded-2xl mb-3'} ${
            tone === 'error' ? 'bg-err/10 text-err' : 'bg-base-800 text-base-500'
          }`}
        >
          <Icon size={compact ? 16 : 20} />
        </div>
      )}
      <p className={`${compact ? 'text-[12px]' : 'text-sm'} font-medium ${
        tone === 'error' ? 'text-err' : 'text-base-200'
      }`}>
        {title}
      </p>
      {description && (
        <p className={`${compact ? 'text-[11px]' : 'text-xs'} text-base-500 mt-1 max-w-xs leading-relaxed`}>
          {description}
        </p>
      )}
      {action && <div className="mt-4">{action}</div>}
    </div>
  )
}