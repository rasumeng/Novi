interface Props {
  icon: React.ElementType
  label: string
  size?: 'sm' | 'md'
}

// Shared visual language for "this thing can do X" across the app — model
// capabilities today (chat/coding/vision/embeddings/reasoning/tools), MCP
// connector capabilities already elsewhere, and future tool/plugin/knowledge
// systems. Deliberately takes plain icon+label props rather than a hardcoded
// capability enum, so a new domain can render through this component without
// ever needing to modify it — it only needs its own {icon, label} metadata map.
export function CapabilityBadge({ icon: Icon, label, size = 'sm' }: Props) {
  const isSmall = size === 'sm'
  return (
    <span
      className={`inline-flex items-center gap-1 rounded-md font-medium bg-base-800 text-base-400 border border-base-700/50 ${
        isSmall ? 'px-1.5 py-0.5 text-[10px]' : 'px-2 py-1 text-[11px]'
      }`}
    >
      <Icon size={isSmall ? 10 : 12} />
      {label}
    </span>
  )
}
