import { AlertTriangle, ArrowUpRight, Landmark } from 'lucide-react'
import { Markdown } from '../components/Markdown'
import { Avatar, Badge, Card, ErrorNote, Page, SectionTitle, Spinner } from '../components/ui'
import { api } from '../lib/api'
import { kindLabel, shortSystem } from '../lib/format'
import { href } from '../lib/router'
import { useAsync } from '../lib/useAsync'

/** Opens any source in-app: imported Slack threads, tickets and docs have no public URL. */
export function SourcePage({ uid }: { uid: string }) {
  const { data, error, loading } = useAsync(() => api.source(uid), [uid])
  if (loading && !data) return <Page title="Source"><Spinner /></Page>
  if (error || !data) return <Page title="Source"><ErrorNote error={error ?? 'Not found'} /></Page>
  return (
    <Page
      title={data.title}
      subtitle={
        <span className="flex flex-wrap items-center gap-1.5">
          <Badge>{kindLabel(data.kind)}</Badge>
          {data.state && <Badge>{data.state}</Badge>}
          {data.date && <span>{data.date}</span>}
          {data.author && (
            <span>
              · by{' '}
              <a href={href('people', data.author)} className="text-accent hover:underline">
                {data.author}
              </a>
            </span>
          )}
          {data.external_url && (
            <a href={data.external_url} target="_blank" rel="noreferrer" className="inline-flex items-center gap-0.5 text-accent hover:underline">
              · Open original <ArrowUpRight className="h-3.5 w-3.5" />
            </a>
          )}
        </span>
      }
    >
      {data.parent && (
        <p className="mb-4 text-sm text-muted">
          Reply in{' '}
          <a href={href('source', data.parent.uid)} className="text-accent hover:underline">
            {data.parent.title}
          </a>
        </p>
      )}

      {(data.decisions.length > 0 || data.challenges.length > 0) && (
        <div className="mb-5 space-y-2">
          {data.decisions.map((d) => (
            <a key={d.uid} href={href('decisions', d.uid)} className="flex items-center gap-2 rounded-lg bg-accent-soft px-3 py-2 text-sm text-fg hover:opacity-90">
              <Landmark className="h-4 w-4 text-accent" />
              <span className="flex-1">{d.title}</span>
              <span className="text-xs text-muted">{d.role}</span>
            </a>
          ))}
          {data.challenges.map((c) => (
            <a key={c.uid + c.premise} href={href('decisions', c.uid)} className="block rounded-lg bg-danger-soft px-3 py-2 text-sm hover:opacity-90">
              <span className="flex items-center gap-1.5 font-medium text-danger">
                <AlertTriangle className="h-4 w-4" /> Challenges “{c.title}”
              </span>
              <span className="mt-0.5 block text-xs text-muted">Assumed: {c.premise}</span>
            </a>
          ))}
        </div>
      )}

      {data.body ? (
        <Card className="p-5">
          <Markdown text={data.body} />
        </Card>
      ) : (
        <p className="text-sm text-muted">No text.</p>
      )}

      {data.replies.length > 0 && (
        <>
          <SectionTitle>Conversation</SectionTitle>
          <div className="space-y-3">
            {data.replies.map((r) => (
              <div key={r.uid} className="flex gap-3">
                {r.author ? <Avatar handle={r.author} size={28} /> : <span className="h-7 w-7" />}
                <div className="min-w-0 flex-1">
                  <p className="text-xs text-faint">
                    {r.author && (
                      <a href={href('people', r.author)} className="font-medium text-fg hover:underline">
                        {r.author}
                      </a>
                    )}{' '}
                    {r.date}
                  </p>
                  <div className="mt-1">
                    <Markdown text={r.body ?? ''} className="text-[14px]" />
                  </div>
                </div>
              </div>
            ))}
          </div>
        </>
      )}

      {data.systems.length > 0 && (
        <>
          <SectionTitle>About</SectionTitle>
          <div className="flex flex-wrap gap-1.5">
            {data.systems.map((s) => (
              <a key={s} href={href('systems', s)} className="rounded-md border border-line bg-surface px-2 py-1 text-sm hover:border-line-strong">
                {shortSystem(s)}
              </a>
            ))}
          </div>
        </>
      )}
    </Page>
  )
}
