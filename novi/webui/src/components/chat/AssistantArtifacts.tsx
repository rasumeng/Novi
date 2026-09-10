import { PlanData } from '@/types'
import { InlinePlanApproval } from './InlinePlanApproval'
import { PermissionPrompt } from '@/components/common/PermissionPrompt'

interface PermissionRequest {
  tool: string
  args: Record<string, unknown>
  id: string
  timeoutMs?: number
  expiresAt?: string
}

export function AssistantArtifacts({
  plan,
  permission,
  onApprovePlan,
  onRejectPlan,
  onAnswerPermission,
  onCancel,
}: {
  plan?: PlanData | null
  permission?: PermissionRequest | null
  onApprovePlan?: () => void
  onRejectPlan?: () => void
  onAnswerPermission?: (allowed: boolean, id: string) => void
  onCancel?: () => void
}) {
  return (
    <div className="w-full space-y-3 mt-1">
      {plan && <InlinePlanApproval plan={plan} onApprove={onApprovePlan} onReject={onRejectPlan} />}
      {permission && (
        <PermissionPrompt
          request={permission}
          onAnswer={(allowed) => onAnswerPermission?.(allowed, permission.id)}
          onCancel={onCancel}
        />
      )}
    </div>
  )
}
