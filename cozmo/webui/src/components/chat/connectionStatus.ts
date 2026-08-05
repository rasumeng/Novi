import { ConnectionState } from '@/services/cozmo'

export const CONNECTION_LABEL: Record<ConnectionState, { text: string; dot: string }> = {
  connecting: { text: 'Connecting…', dot: 'bg-amber-400' },
  open: { text: 'Connected', dot: 'bg-emerald-400' },
  closed: { text: 'Disconnected — retrying', dot: 'bg-red-400' },
}
