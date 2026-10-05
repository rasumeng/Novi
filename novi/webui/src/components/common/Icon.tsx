import { LucideIcon } from 'lucide-react'

type IconSize = 'inline' | 'standalone' | 'hero'

const sizeMap: Record<IconSize, number> = {
  inline: 14,
  standalone: 16,
  hero: 20,
}

interface Props {
  icon: LucideIcon
  size?: IconSize
  className?: string
  'aria-hidden'?: boolean
}

export function Icon({ icon: IconComponent, size = 'standalone', className = '', 'aria-hidden': ariaHidden = true }: Props) {
  const iconSize = sizeMap[size]
  return <IconComponent size={iconSize} className={className} aria-hidden={ariaHidden} />
}