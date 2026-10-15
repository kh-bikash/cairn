import { Check, ExternalLink, FileText, GitPullRequest, MessageSquare, SquarePen, X } from 'lucide-react'
import { useState } from 'react'
import { api } from '../lib/api'
import type { Action } from '../lib/types'
import { Markdown } from './Markdown'
import { Badge, Button, Card, ErrorNote } from './ui'

const STATUS_TONE = { pending: 'warn', running: 'accent', done: 'ok', noop: 'neutral', failed: 'danger', rejected: 'neutral' } as const
const KIND_ICON = { wiki_pr: GitPullRequest, doc_pr: GitPullRequest, issue_comment: MessageSquare, issue_create: SquarePen } as const

/** A proposed write with its full preview. Nothing reaches GitHub until a person approves it. */
export function ActionCard({ action, onChange, defaultOpen = false }: { action: Action; onChange?: (a: Action) => void; defaultOpen?: boolean }) {
  const [busy, setBusy] = useState<'approve' | 'reject' | null>(null)
  const [error, setError] = useState<unknown>(null)
  const [open, setOpen] = useState(defaultOpen || action.status === 'pending')
  const Icon = KIND_ICON[action.kind as keyof typeof KIND_ICON] ?? FileText

  const act = (kind: 'approve' | 'reject') => {
    setBusy(kind)
    setError(null)
    ;(kind === 'approve' ? api.approve(action.uid) : api.reject(action.uid))
      .then((a) => onChange?.(a))
      .catch(setError)
      .finally(() => setBusy(null))
  }

  return (
    <Card className="p-4">
      <div className="flex items-start gap-3">
        <span className="mt-0.5 rounded-lg bg-surface-2 p-1.5 text-muted">
          <Icon className="h-4 w-4" />
        </span>
        <div className="min-w-0 flex-1">
          <p className="text-sm font-medium text-fg">{action.title}</p>
          <p className="text-xs text-muted">
            {action.repo} · {new Date(action.ts).toLocaleString()}
          </p>
        </div>
        <Badge tone={STATUS_TONE[action.status as keyof typeof STATUS_TONE] ?? 'neutral'}>{action.status}</Badge>
      </div>
      <button onClick={() => setOpen((o) => !o)} className="mt-3 text-xs font-medium text-muted hover:text-fg">
        {open ? 'Hide preview' : 'Show preview'}
      </button>
      {open && (
        <div className="mt-2 max-h-80 overflow-auto rounded-lg border border-line bg-surface-2 p-3 scroll-thin">
          <Markdown text={action.preview} className="text-[13px]" />
        </div>
      )}
      {action.status === 'pending' && (
        <div className="mt-3 flex gap-2">
          <Button variant="primary" icon={<Check className="h-4 w-4" />} loading={busy === 'approve'} disabled={busy !== null} onClick={() => act('approve')}>
            Approve &amp; publish
          </Button>
          <Button icon={<X className="h-4 w-4" />} loading={busy === 'reject'} disabled={busy !== null} onClick={() => act('reject')}>
            Reject
          </Button>
        </div>
      )}
      {action.result_url && (
        <a href={action.result_url} target="_blank" rel="noreferrer" className="mt-3 inline-flex items-center gap-1 text-sm text-accent hover:underline">
          View on GitHub <ExternalLink className="h-3.5 w-3.5" />
        </a>
      )}
      {(error || action.error) && (
        <div className="mt-3">
          <ErrorNote error={error ?? action.error} />
        </div>
      )}
    </Card>
  )
}
