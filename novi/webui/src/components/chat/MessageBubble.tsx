import { ChatMessage } from '@/types'
import { UserMessage } from './UserMessage'
import { AssistantResponse } from './AssistantResponse'

// Deprecated: use UserMessage / AssistantResponse directly. Kept for backward compat / tests.
export function MessageBubble({ message }: { message: ChatMessage }) {
  return message.role === 'user' ? <UserMessage message={message} /> : <AssistantResponse message={message} />
}
