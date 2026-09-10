import { MessageContent } from './MessageContent'

export function AssistantContent({ content, streaming }: { content: string; streaming?: boolean }) {
  return <MessageContent content={content} streaming={streaming} />
}
