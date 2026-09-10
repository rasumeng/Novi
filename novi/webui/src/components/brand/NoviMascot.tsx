import NoviMascotDefault from '@/assets/brand/mascot/Novi Mascot.svg'
import NoviMascotHappy from '@/assets/brand/mascot/Novi-happy.svg'

export type NoviExpression = 'default' | 'happy'

interface NoviMascotProps {
  expression?: NoviExpression
  className?: string
  size?: number
  alt?: string
}

const EXPRESSIONS = {
  default: NoviMascotDefault,
  happy: NoviMascotHappy,
}

export function NoviMascot({
  expression = 'default',
  className = '',
  size = 80,
  alt = 'Novi',
}: NoviMascotProps) {
  return (
    <img
      src={EXPRESSIONS[expression]}
      alt={alt}
      width={size}
      height={size}
      className={className}
      draggable={false}
    />
  )
}