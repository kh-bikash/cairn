import { Check, ExternalLink, FileText, GitPullRequest, Loader2, MessageSquare, RefreshCw, X } from 'lucide-react'
import { useCallback, useEffect, useState } from 'react'
import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import { api } from '../api'
import type { Action } from '../types'

const STATUS_STYLE: Record<string, string> = {
  pending: 'bg-amber-400/15 text-amber-200',
  running: 'bg-sky-400/15 text-sky-200',
  done: 'bg-emerald-400/15 text-emerald-200',
  noop: 'bg-slate-400/15 text-slate-300',
  failed: 'bg-red-400/15 text-red-200',
  rejected: 'bg-slate-500/15 text-slate-400',
}

/** One proposal with its preview and the human approval controls. */
export function ActionCard({ action, onChange, compact = false }: { action: Action; onChange?: (a: Action) => void; compact?: boolean }) {
  const [busy, setBusy] = useState<'approve' | 'reject' | null>(null)
  const [err, setErr] = useState<string | null>(null)
  const [open, setOpen] = useState(!compact)
  const Icon = action.kind === 'wiki_pr' ? GitPullRequest : MessageSquare

  const act = (kind: 'approve' | 'reject') => {
    setBusy(kind)
    setErr(null)
    ;(kind === 'approve' ? api.approve(action.uid) : api.reject(action.uid))
      .then((a) => onChange?.(a))
      .catch((e: Error) => setErr(e.message))
      .finally(() => setBusy(null))
  }

  return (
    <div className="rounded-lg border border-ink-600 bg-ink-800 p-3">
      <div className="flex items-start gap-2">
        <Icon className="mt-0.5 h-4 w-4 shrink-0 text-teal-300" />
        <div className="min-w-0 flex-1">
          <p className="text-sm font-medium text-slate-100">{action.title}</p>
          <p className="text-[11px] text-slate-500">
            {action.repo} · {new Date(action.ts).toLocaleString()}
          </p>
        </div>
        <span className={`rounded-full px-2 py-0.5 text-[10px] font-medium uppercase ${STATUS_STYLE[action.status] ?? STATUS_STYLE.noop}`}>{action.status}</span>
      </div>
      <button onClick={() => setOpen((o) => !o)} className="mt-2 flex items-center gap-1 text-[11px] text-slate-400 hover:text-slate-200">
        <FileText className="h-3 w-3" /> {open ? 'Hide' : 'Show'} preview
      </button>
      {open && (
        <div className="answer mt-2 max-h-72 overflow-auto rounded-md bg-ink-900 p-2.5 text-xs leading-relaxed text-slate-300">
          <ReactMarkdown remarkPlugins={[remarkGfm]}>{action.preview}</ReactMarkdown>
        </div>
      )}
      {action.status === 'pending' && (
        <div className="mt-3 flex gap-2">
          <button
            onClick={() => act('approve')}
            disabled={busy !== null}
            className="inline-flex items-center gap-1.5 rounded-md bg-teal-400 px-3 py-1.5 text-xs font-semibold text-ink-950 hover:bg-teal-300 disabled:opacity-50"
          >
            {busy === 'approve' ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <Check className="h-3.5 w-3.5" />} Approve &amp; publish
          </button>
          <button
            onClick={() => act('reject')}
            disabled={busy !== null}
            className="inline-flex items-center gap-1.5 rounded-md bg-ink-600 px-3 py-1.5 text-xs text-slate-200 hover:bg-ink-700 disabled:opacity-50"
          >
            <X className="h-3.5 w-3.5" /> Reject
          </button>
        </div>
      )}
      {action.result_url && (
        <a href={action.result_url} target="_blank" rel="noreferrer" className="mt-2 inline-flex items-center gap-1 text-xs text-teal-300 hover:underline">
          View on GitHub <ExternalLink className="h-3 w-3" />
        </a>
      )}
      {(err || action.error) && <p className="mt-2 text-xs text-red-300">{err ?? action.error}</p>}
    </div>
  )
}

export function ActionsPanel() {
  const [actions, setActions] = useState<Action[]>([])
  const [repos, setRepos] = useState<string[]>([])
  const [wikiRepo, setWikiRepo] = useState<string | null>(null)
  const [issue, setIssue] = useState('')
  const [busy, setBusy] = useState<string | null>(null)
  const [err, setErr] = useState<string | null>(null)

  const load = useCallback(() => {
    api
      .actions()
      .then((r) => {
        setActions(r.actions)
        setRepos(r.allowed_repos)
        setWikiRepo(r.wiki_repo)
      })
      .catch((e: Error) => setErr(e.message))
  }, [])
  useEffect(() => {
    load()
  }, [load])

  const run = (label: string, fn: () => Promise<Action>) => {
    setBusy(label)
    setErr(null)
    fn()
      .then((a) => setActions((xs) => [a, ...xs]))
      .catch((e: Error) => setErr(e.message))
      .finally(() => setBusy(null))
  }

  const replace = (a: Action) => setActions((xs) => xs.map((x) => (x.uid === a.uid ? a : x)))
  const issueNumber = Number(issue.replace('#', ''))

  return (
    <div className="space-y-3">
      <section className="space-y-2 rounded-lg border border-ink-600 bg-ink-800 p-3">
        <header className="flex items-center justify-between">
          <h3 className="text-xs font-semibold uppercase tracking-wide text-slate-400">Propose an action</h3>
          <button onClick={load} aria-label="Refresh actions" className="text-slate-500 hover:text-slate-300">
            <RefreshCw className="h-3 w-3" />
          </button>
        </header>
        <p className="text-[11px] leading-relaxed text-slate-500">
          Cairn only proposes. Nothing reaches GitHub until you approve it, and writes are limited to {repos.join(', ') || 'no repos (set CAIRN_ACTION_REPOS)'}.
        </p>
        <button
          disabled={!wikiRepo || busy !== null}
          onClick={() => run('wiki', () => api.proposeWiki('Refresh the knowledge base from the graph'))}
          className="flex w-full items-center justify-center gap-1.5 rounded-md bg-teal-400/15 px-3 py-2 text-xs text-teal-100 ring-1 ring-teal-400/30 hover:bg-teal-400/25 disabled:opacity-40"
        >
          {busy === 'wiki' ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <GitPullRequest className="h-3.5 w-3.5" />} Update the OKF wiki
        </button>
        <form
          className="flex gap-2"
          onSubmit={(e) => {
            e.preventDefault()
            if (repos[0] && issueNumber > 0) run('route', () => api.proposeRoute(repos[0], issueNumber))
          }}
        >
          <input
            value={issue}
            onChange={(e) => setIssue(e.target.value)}
            placeholder={`Issue # in ${repos[0] ?? 'sandbox'}`}
            className="min-w-0 flex-1 rounded-md border border-ink-600 bg-ink-900 px-2 py-1.5 text-xs text-slate-100 placeholder:text-slate-500 focus:border-teal-400/60 focus:outline-none"
            aria-label="Issue number"
          />
          <button
            type="submit"
            disabled={!repos[0] || !(issueNumber > 0) || busy !== null}
            className="inline-flex items-center gap-1.5 rounded-md bg-ink-600 px-3 py-1.5 text-xs text-slate-100 hover:bg-ink-700 disabled:opacity-40"
          >
            {busy === 'route' ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <MessageSquare className="h-3.5 w-3.5" />} Route
          </button>
        </form>
        {err && <p className="text-xs text-red-300">{err}</p>}
      </section>
      {actions.length === 0 && <p className="p-2 text-sm text-slate-500">No proposals yet. Ask the agent to “update the wiki” or “route issue #5”.</p>}
      {actions.map((a) => (
        <ActionCard key={a.uid} action={a} onChange={replace} compact={a.status !== 'pending'} />
      ))}
    </div>
  )
}
