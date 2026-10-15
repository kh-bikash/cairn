import { AlertTriangle, ArrowLeft, Ban, Landmark, MessagesSquare, Search } from 'lucide-react'
import { useEffect, useState } from 'react'
import { GraphView } from '../components/GraphView'
import { Avatar, Badge, Card, Empty, ErrorNote, Input, Page, SectionTitle, Spinner } from '../components/ui'
import { api } from '../lib/api'
import { kindLabel, shortSystem } from '../lib/format'
import { href, sourceHref } from '../lib/router'
import type { Theme } from '../lib/theme'
import { useAsync } from '../lib/useAsync'

export function DecisionsPage({ id, theme }: { id: string | null; theme: Theme }) {
  return id ? <DecisionDetailView uid={id} theme={theme} /> : <DecisionList />
}

function DecisionList() {
  const [query, setQuery] = useState('')
  const [debounced, setDebounced] = useState('')
  const [driftOnly, setDriftOnly] = useState(false)
  useEffect(() => {
    const t = setTimeout(() => setDebounced(query), 250)
    return () => clearTimeout(t)
  }, [query])
  const { data, error, loading } = useAsync(() => api.decisions({ q: debounced, drift: driftOnly, limit: 100 }), [debounced, driftOnly])

  return (
    <Page title="Decisions" subtitle="Every decision Cairn found, with the reasons behind it and where each reason came from.">
      <div className="mb-5 flex flex-wrap items-center gap-2">
        <div className="relative min-w-60 flex-1">
          <Search className="pointer-events-none absolute left-3 top-2.5 h-4 w-4 text-faint" />
          <Input value={query} onChange={(e) => setQuery(e.target.value)} placeholder="Search decisions and their reasons" className="pl-9" aria-label="Search decisions" />
        </div>
        <label className="inline-flex h-9 cursor-pointer items-center gap-2 rounded-lg border border-line bg-surface px-3 text-sm text-fg">
          <input type="checkbox" checked={driftOnly} onChange={(e) => setDriftOnly(e.target.checked)} className="accent-[var(--accent)]" />
          Only challenged
        </label>
      </div>
      <ErrorNote error={error} />
      {loading && !data ? (
        <Spinner />
      ) : data && data.items.length === 0 ? (
        <Empty title={debounced || driftOnly ? 'No matching decisions' : 'No decisions yet'} icon={<Landmark className="h-6 w-6" />}>
          {debounced || driftOnly ? 'Try different words.' : 'Run extraction to find decisions in PRs, threads, tickets and docs.'}
        </Empty>
      ) : (
        <>
          <p className="mb-2 text-xs text-faint">{data?.total.toLocaleString()} decisions</p>
          <ul className="divide-y divide-line overflow-hidden rounded-xl border border-line bg-surface">
            {data?.items.map((d) => (
              <li key={d.uid}>
                <a href={href('decisions', d.uid)} className="block px-4 py-3.5 transition hover:bg-surface-2">
                  <div className="flex items-start gap-3">
                    <div className="min-w-0 flex-1">
                      <p className="text-sm font-medium text-fg">{d.title}</p>
                      <p className="mt-1 line-clamp-2 text-[13px] leading-relaxed text-muted">{d.summary}</p>
                      <div className="mt-2 flex flex-wrap items-center gap-1.5 text-xs text-faint">
                        {d.drift && (
                          <Badge tone="danger">
                            <AlertTriangle className="h-3 w-3" /> Challenged
                          </Badge>
                        )}
                        <Badge>{d.category}</Badge>
                        <span>{d.date}</span>
                        <span>· {d.reasons} reasons</span>
                        {d.deciders.length > 0 && <span>· {d.deciders.slice(0, 3).join(', ')}</span>}
                      </div>
                    </div>
                  </div>
                </a>
              </li>
            ))}
          </ul>
        </>
      )}
    </Page>
  )
}

function DecisionDetailView({ uid, theme }: { uid: string; theme: Theme }) {
  const { data, error, loading } = useAsync(() => api.decision(uid), [uid])
  if (loading && !data) return <Page title="Decision"><Spinner /></Page>
  if (error || !data) return <Page title="Decision"><ErrorNote error={error ?? 'Not found'} /></Page>
  const d = data.decision
  const thread = sourceHref(d.decided_in, d.thread?.url)
  // Show readable titles, not uids; sources the API returned carry their titles.
  const titleOf = (uid: string) => d.sources.find((s) => s.uid === uid)?.title ?? uid
  return (
    <Page
      title={d.title}
      subtitle={
        <span className="flex flex-wrap items-center gap-1.5">
          {d.drift_detected && (
            <Badge tone="danger">
              <AlertTriangle className="h-3 w-3" /> Reasons challenged
            </Badge>
          )}
          {d.status === 'superseded' && <Badge tone="warn">Superseded</Badge>}
          <Badge>{d.category}</Badge>
          <span>Decided {d.date}</span>
          <span>
            · in{' '}
            <a href={thread.href} target={thread.external ? '_blank' : undefined} rel="noreferrer" className="text-accent hover:underline">
              {d.thread?.title ?? d.decided_in}
            </a>
          </span>
        </span>
      }
      actions={
        <a href={href('decisions')} className="inline-flex items-center gap-1 text-sm text-muted hover:text-fg">
          <ArrowLeft className="h-4 w-4" /> All decisions
        </a>
      }
    >
      {d.summary && <p className="text-[15px] leading-relaxed text-fg">{d.summary}</p>}

      <SectionTitle>Why</SectionTitle>
      {d.premises.length === 0 ? (
        <p className="text-sm text-muted">No reasons were recorded with a verifiable quote.</p>
      ) : (
        <div className="space-y-3">
          {d.premises.map((p, i) => {
            const src = sourceHref(p.source, d.sources.find((s) => s.uid === p.source)?.url)
            return (
              <Card key={i} className="p-4">
                <p className="text-sm font-medium text-fg">{p.claim}</p>
                <blockquote className="mt-2 border-l-2 border-accent/50 pl-3 text-[13px] leading-relaxed text-muted">“{p.quote}”</blockquote>
                <a href={src.href} target={src.external ? '_blank' : undefined} rel="noreferrer" className="mt-2 inline-block text-xs text-accent hover:underline">
                  {titleOf(p.source)}
                </a>
                {p.challenged_by?.map((c, j) => {
                  const cs = sourceHref(c.source)
                  return (
                    <div key={j} className="mt-3 rounded-lg bg-danger-soft p-3">
                      <p className="flex items-center gap-1.5 text-xs font-semibold text-danger">
                        <AlertTriangle className="h-3.5 w-3.5" /> Challenged on {c.date}
                      </p>
                      <p className="mt-1 text-[13px] text-fg">“{c.quote}”</p>
                      {c.why && <p className="mt-1 text-xs text-muted">{c.why}</p>}
                      <a href={cs.href} className="mt-1 inline-block text-xs text-danger hover:underline">
                        {titleOf(c.source)}
                      </a>
                    </div>
                  )
                })}
              </Card>
            )
          })}
        </div>
      )}

      {d.rejected_alternatives.length > 0 && (
        <>
          <SectionTitle>Alternatives rejected</SectionTitle>
          <ul className="space-y-2">
            {d.rejected_alternatives.map((r, i) => (
              <li key={i} className="flex gap-2.5 text-sm">
                <Ban className="mt-0.5 h-4 w-4 shrink-0 text-faint" />
                <span>
                  <span className="font-medium text-fg">{r.option}</span>
                  <span className="text-muted">: {r.claim}</span>
                </span>
              </li>
            ))}
          </ul>
        </>
      )}

      {d.also_discussed_in.length > 0 && (
        <>
          <SectionTitle>Trail</SectionTitle>
          <ul className="space-y-1.5">
            {[{ uid: d.decided_in, kind: 'decided', title: d.thread?.title ?? d.decided_in, date: d.date }, ...d.also_discussed_in]
              .sort((a, b) => String(a.date).localeCompare(String(b.date)))
              .map((s) => {
                const link = sourceHref(s.uid, d.sources.find((x) => x.uid === s.uid)?.url)
                return (
                  <li key={s.uid} className="flex items-center gap-2.5 text-sm">
                    <MessagesSquare className="h-4 w-4 text-faint" />
                    <span className="w-24 shrink-0 text-xs text-faint">{s.date}</span>
                    <a href={link.href} target={link.external ? '_blank' : undefined} rel="noreferrer" className="min-w-0 flex-1 truncate text-fg hover:underline">
                      {s.title}
                    </a>
                    <Badge>{s.kind === 'decided' ? 'Decided here' : kindLabel(s.kind)}</Badge>
                  </li>
                )
              })}
          </ul>
        </>
      )}

      <div className="mt-7 grid gap-6 sm:grid-cols-2">
        <div>
          <SectionTitle>Decided by</SectionTitle>
          <div className="flex flex-wrap gap-2">
            {d.deciders.length === 0 && <span className="text-sm text-muted">Unknown</span>}
            {d.deciders.map((h) => (
              <a key={h} href={href('people', h)} className="inline-flex items-center gap-2 rounded-full border border-line bg-surface py-1 pl-1 pr-3 text-sm hover:border-line-strong">
                <Avatar handle={h} size={22} /> {h}
              </a>
            ))}
          </div>
        </div>
        <div>
          <SectionTitle>Affects</SectionTitle>
          <div className="flex flex-wrap gap-1.5">
            {d.systems.length === 0 && <span className="text-sm text-muted">No systems recorded</span>}
            {d.systems.map((s) => (
              <a key={s} href={href('systems', s)} className="rounded-md border border-line bg-surface px-2 py-1 text-sm hover:border-line-strong">
                {shortSystem(s)}
              </a>
            ))}
          </div>
        </div>
      </div>

      {(d.superseded_by.length > 0 || d.related.length > 0) && (
        <>
          <SectionTitle>Related decisions</SectionTitle>
          <ul className="space-y-1">
            {d.superseded_by.map((s) => (
              <li key={s.uid} className="text-sm">
                <Badge tone="warn">Superseded by</Badge>{' '}
                <a href={href('decisions', s.uid)} className="text-fg hover:underline">
                  {s.title}
                </a>
              </li>
            ))}
            {d.related.map((r) => (
              <li key={r.uid} className="text-sm">
                <a href={href('decisions', r.uid)} className="text-fg hover:underline">
                  {r.title}
                </a>{' '}
                <span className="text-xs text-faint">{r.date}</span>
              </li>
            ))}
          </ul>
        </>
      )}

      <SectionTitle>Graph</SectionTitle>
      <GraphView graph={data.graph} theme={theme} height={360} focus={`Decision:${d.uid}`} />
    </Page>
  )
}
