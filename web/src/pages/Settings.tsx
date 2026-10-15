import { CheckCircle2, KeyRound, RefreshCw, XCircle } from 'lucide-react'
import { useEffect, useState, type FormEvent } from 'react'
import { Badge, Button, Card, ErrorNote, Input, KeyValue, Page, SectionTitle } from '../components/ui'
import { api, session } from '../lib/api'
import type { Health, WorkspaceInfo } from '../lib/types'
import { useAsync } from '../lib/useAsync'

export function SettingsPage({ health, workspace }: { health: Health | null; workspace: WorkspaceInfo | undefined }) {
  const sync = useAsync(() => api.sync(), [])
  const [repos, setRepos] = useState('')
  const [syncError, setSyncError] = useState<unknown>(null)
  const [starting, setStarting] = useState(false)
  const [token, setToken] = useState(session.adminToken ?? '')
  const [saved, setSaved] = useState(false)
  const job = sync.data?.job
  const running = job && (job.status === 'queued' || job.status === 'running')

  // Poll while a sync is running.
  useEffect(() => {
    if (!running) return
    const t = setInterval(sync.reload, 2_000)
    return () => clearInterval(t)
  }, [running, sync.reload])

  const startSync = (e: FormEvent) => {
    e.preventDefault()
    const list = repos.split(/[\s,]+/).map((r) => r.trim()).filter(Boolean)
    if (!list.length) return
    setStarting(true)
    setSyncError(null)
    api
      .startSync(list)
      .then(() => {
        setRepos('')
        sync.reload()
      })
      .catch(setSyncError)
      .finally(() => setStarting(false))
  }

  const isGithubWorkspace = !workspace || workspace.graph === (health?.graph ?? 'cairn')
  const mcp = `{
  "mcpServers": {
    "cairn": { "command": "uv", "args": ["--directory", "<path>/company-brain/backend", "run", "cairn", "mcp"] }
  }
}`

  return (
    <Page title="Settings" subtitle={workspace ? `${workspace.name}: ${workspace.description}` : undefined}>
      <SectionTitle>AI models</SectionTitle>
      <Card className="divide-y divide-line px-4">
        {health &&
          Object.entries(health.llm).map(([role, m]) => (
            <KeyValue key={role} label={role === 'decide' ? 'Quick checks' : role === 'extract' ? 'Reading decisions' : 'Answering'}>
              <span className="inline-flex items-center gap-1.5">
                {m.ready ? <CheckCircle2 className="h-4 w-4 text-ok" /> : <XCircle className="h-4 w-4 text-danger" />}
                <span className="font-mono text-xs">{m.model.split(',')[0]}</span>
                {m.model.includes(',') && <Badge>+{m.model.split(',').length - 1} fallbacks</Badge>}
              </span>
            </KeyValue>
          ))}
      </Card>

      <SectionTitle>Admin key</SectionTitle>
      <Card className="p-4">
        <p className="text-sm text-muted">
          {health?.admin_required
            ? 'This deployment requires an admin key to approve actions and start syncs.'
            : 'This deployment does not require an admin key (local mode).'}
        </p>
        <form
          className="mt-3 flex gap-2"
          onSubmit={(e) => {
            e.preventDefault()
            session.setAdminToken(token.trim() || null)
            setSaved(true)
            setTimeout(() => setSaved(false), 1500)
          }}
        >
          <Input type="password" value={token} onChange={(e) => setToken(e.target.value)} placeholder="Admin key" aria-label="Admin key" />
          <Button type="submit" icon={<KeyRound className="h-4 w-4" />}>
            {saved ? 'Saved' : 'Save'}
          </Button>
        </form>
        <p className="mt-2 text-xs text-faint">Stored only in this browser.</p>
      </Card>

      <SectionTitle>Data</SectionTitle>
      <Card className="p-4">
        {isGithubWorkspace ? (
          <>
            <p className="text-sm text-muted">Connected repositories ({sync.data?.repos.length ?? 0}):</p>
            <div className="mt-2 flex flex-wrap gap-1.5">
              {sync.data?.repos.map((r) => (
                <Badge key={r}>{r}</Badge>
              ))}
            </div>
            <form onSubmit={startSync} className="mt-4 flex gap-2">
              <Input value={repos} onChange={(e) => setRepos(e.target.value)} placeholder="owner/repo, owner/other-repo" aria-label="Repositories to connect" />
              <Button type="submit" icon={<RefreshCw className="h-4 w-4" />} loading={starting} disabled={!!running || !sync.data?.github_ready}>
                Connect &amp; sync
              </Button>
            </form>
            {!sync.data?.github_ready && <p className="mt-2 text-xs text-warn">The server has no GitHub token, so syncing is disabled.</p>}
            <div className="mt-2">
              <ErrorNote error={syncError} />
            </div>
            {job && (
              <div className="mt-3 rounded-lg bg-surface-2 p-3 text-sm">
                <p className="flex items-center gap-2">
                  <Badge tone={job.status === 'done' ? 'ok' : job.status === 'failed' ? 'danger' : 'accent'}>{job.status}</Badge>
                  <span className="text-fg">{job.repos.join(', ')}</span>
                </p>
                <p className="mt-1 text-xs text-muted">
                  {job.stage && `${job.stage}: `}
                  {job.detail}
                </p>
                {job.error && <p className="mt-1 text-xs text-danger">{job.error}</p>}
              </div>
            )}
          </>
        ) : (
          <p className="text-sm text-muted">
            This workspace was imported from company exports (Slack, Jira/Linear CSV, docs, org chart). Re-import with{' '}
            <code className="rounded bg-surface-2 px-1 font-mono text-xs">cairn workspace import &lt;folder&gt;</code>.
          </p>
        )}
      </Card>

      <SectionTitle>Use Cairn from Claude Desktop or Cursor (MCP)</SectionTitle>
      <Card className="p-4">
        <p className="text-sm text-muted">Every graph tool is available over MCP. Add this to your MCP client config:</p>
        <pre className="mt-3 overflow-x-auto rounded-lg bg-surface-2 p-3 font-mono text-xs text-fg">{mcp}</pre>
      </Card>
    </Page>
  )
}
