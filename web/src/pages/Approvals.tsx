import { GitPullRequest, Inbox, MessageSquare } from 'lucide-react'
import { useState, type FormEvent } from 'react'
import { ActionCard } from '../components/ActionCard'
import { Button, Card, Empty, ErrorNote, Input, Page, SectionTitle, Spinner } from '../components/ui'
import { api } from '../lib/api'
import type { Action } from '../lib/types'
import { useAsync } from '../lib/useAsync'

export function ApprovalsPage({ onChanged }: { onChanged: () => void }) {
  const { data, error, loading, reload } = useAsync(() => api.actions(), [])
  const [issue, setIssue] = useState('')
  const [busy, setBusy] = useState<string | null>(null)
  const [proposeError, setProposeError] = useState<unknown>(null)
  const [extra, setExtra] = useState<Action[]>([])

  const propose = (label: string, p: Promise<Action>) => {
    setBusy(label)
    setProposeError(null)
    p.then((a) => {
      setExtra((xs) => [a, ...xs])
      onChanged()
    })
      .catch(setProposeError)
      .finally(() => setBusy(null))
  }
  const repo = data?.allowed_repos[0]
  const number = Number(issue.replace('#', ''))
  const actions = [...extra, ...(data?.actions ?? []).filter((a) => !extra.some((x) => x.uid === a.uid))]
  const pending = actions.filter((a) => a.status === 'pending')
  const done = actions.filter((a) => a.status !== 'pending')

  return (
    <Page title="Approvals" subtitle="Everything Cairn wants to publish waits here. Nothing reaches GitHub until a person approves it.">
      <Card className="p-4">
        <p className="text-sm text-muted">
          Writes are limited to <span className="font-medium text-fg">{data?.allowed_repos.join(', ') || 'no repositories (set CAIRN_ACTION_REPOS)'}</span>.
        </p>
        <div className="mt-3 flex flex-wrap gap-2">
          <Button icon={<GitPullRequest className="h-4 w-4" />} loading={busy === 'wiki'} disabled={!data?.wiki_repo || busy !== null} onClick={() => propose('wiki', api.proposeWiki('Refresh the knowledge base from the graph'))}>
            Propose wiki update
          </Button>
          <form
            className="flex gap-2"
            onSubmit={(e: FormEvent) => {
              e.preventDefault()
              if (repo && number > 0) propose('route', api.proposeRoute(repo, number))
            }}
          >
            <Input value={issue} onChange={(e) => setIssue(e.target.value)} placeholder="Issue #" className="w-28" aria-label="Issue number" />
            <Button type="submit" icon={<MessageSquare className="h-4 w-4" />} loading={busy === 'route'} disabled={!repo || !(number > 0) || busy !== null}>
              Route issue
            </Button>
          </form>
        </div>
        <div className="mt-3">
          <ErrorNote error={proposeError} />
        </div>
      </Card>
      <ErrorNote error={error} />
      {loading && !data ? (
        <Spinner />
      ) : actions.length === 0 ? (
        <div className="mt-6">
          <Empty title="Nothing waiting" icon={<Inbox className="h-6 w-6" />}>
            Ask Cairn to update the wiki, route an issue, send a change heads-up or publish a handoff plan.
          </Empty>
        </div>
      ) : (
        <>
          {pending.length > 0 && <SectionTitle>Waiting for approval</SectionTitle>}
          <div className="space-y-3">
            {pending.map((a) => (
              <ActionCard key={a.uid} action={a} onChange={() => { reload(); onChanged() }} />
            ))}
          </div>
          {done.length > 0 && <SectionTitle>History</SectionTitle>}
          <div className="space-y-3">
            {done.map((a) => (
              <ActionCard key={a.uid} action={a} />
            ))}
          </div>
        </>
      )}
    </Page>
  )
}
