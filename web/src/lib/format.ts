export function shortSystem(key: string): string {
  if (key.includes(':')) return key.split(':').pop() ?? key
  return key.split('/').pop() ?? key
}

export function pct(x: number): string {
  return `${Math.round(x * 100)}%`
}

const KIND_LABEL: Record<string, string> = {
  pr: 'Pull request',
  issue: 'Issue',
  discussion: 'Discussion',
  doc: 'Doc',
  adr: 'ADR',
  rfc: 'RFC',
  postmortem: 'Postmortem',
  runbook: 'Runbook',
  meeting: 'Meeting notes',
  thread: 'Slack thread',
  message: 'Slack message',
  ticket: 'Ticket',
  comment: 'Comment',
  review: 'Review',
}

export function kindLabel(kind: string): string {
  return KIND_LABEL[kind] ?? kind
}

export function plural(n: number, word: string): string {
  return `${n.toLocaleString()} ${word}${n === 1 ? '' : 's'}`
}
