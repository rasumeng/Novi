import { useCallback, useRef, useState } from 'react'
import { ConfirmDialog, ConfirmRequest } from '@/components/common/ConfirmDialog'

// Promise-based confirmation: `await confirm({...})` resolves true/false.
// Render `dialog` once near the root of whatever tree calls `confirm`.
export function useConfirm() {
  const [request, setRequest] = useState<ConfirmRequest | null>(null)
  const resolveRef = useRef<((v: boolean) => void) | null>(null)

  const confirm = useCallback((req: ConfirmRequest) => {
    setRequest(req)
    return new Promise<boolean>((resolve) => {
      resolveRef.current = resolve
    })
  }, [])

  const settle = (result: boolean) => {
    setRequest(null)
    resolveRef.current?.(result)
    resolveRef.current = null
  }

  const dialog = request ? (
    <ConfirmDialog
      request={request}
      onConfirm={() => settle(true)}
      onCancel={() => settle(false)}
    />
  ) : null

  return { confirm, dialog }
}
