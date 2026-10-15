import { AlertTriangle, ArrowDownLeft, ArrowLeft, ArrowUpRight, Boxes, GitCompareArrows } from 'lucide-react'
import { GraphView } from '../components/GraphView'
import { Avatar, Badge, Button, Card, Empty, ErrorNote, Page, SectionTitle, Spinner } from '../components/ui'
import { api } from '../lib/api'
import { kindLabel, pct, shortSystem } from '../lib/format'
import { go, href, sourceHref } from '../lib/router'
import type { Theme } from '../lib/theme'
import type { Owner } from '../lib/types'
import { useAsync } from '../lib/useAsync'

export function SystemsPage({ id, theme }: { id: string | null; theme: Theme }) {
  return id ? <SystemView systemKey={id} theme={theme} /> : <SystemList />
}

function Owners({ owners }: { owners: Owner[] }) {
  return (
    <span className="flex -space-x-1.5">
      {owners.slice(0, 3).map((o) => (
        <span key={o.handle} title={`${o.handle} · ${pct(o.share)}`} className="rounded-full ring-2 ring-[var(--surface)]">
          <Avatar handle={o.handle} size={24} />
        </span>
      ))}
    </span>
  )
}

function Risk({ busFactor, concentration }: { busFactor: number; concentration: number }) {
  if (busFactor === 1 || concentration >= 0.6) return <Badge tone="warn">Bus factor 1</Badge>
  if (busFactor === 0) return <Badge>No clear owner</Badge>
  return <Badge tone="ok">Bus factor {busFactor}</Badge>
}

function SystemList() {
  const { data, error, loading } = useAsync(() => api.systems(), [])
  return (
    <Page title="Systems" subtitle="Repositories, services and modules, with who owns them and how concentrated that knowledge is." wide>
      <ErrorNote error={error} />
      {loading && !data ? (
        <Spinner />
      ) : !data || data.items.length === 0 ? (
        <Empty title="No systems yet" icon={<Boxes className="h-6 w-6" />} />
      ) : (
        <div className="space-y-3">
          {data.items.map((s) => (
            <Card key={s.key} className="overflow-hidden">
              <a href={href('systems', s.key)} className="flex flex-wrap items-center gap-3 px-4 py-3.5 hover:bg-surface-2">
                <div className="min-w-0 flex-1">
                  <p className="text-sm font-semibold text-fg">{s.name}</p>
                  {s.description && <p className="mt-0.5 line-clamp-1 text-xs text-muted">{s.description}</p>}
                </div>
                <span className="text-xs text-faint">
                  {s.depended_on_by.length > 0 && `${s.depended_on_by.length} dependents · `}
                  {s.decisions} decisions
                </span>
                <Risk busFactor={s.bus_factor} concentration={s.concentration} />
                <Owners owners={s.owners} />
              </a>
              {s.modules.length > 0 && (
                <div className="grid border-t border-line sm:grid-cols-2 lg:grid-cols-3">
                  {s.modules.slice(0, 9).map((m) => (
                    <a key={m.key} href={href('systems', m.key)} className="flex items-center gap-2 border-b border-line px-4 py-2 text-sm hover:bg-surface-2 sm:border-r">
                      <span className="min-w-0 flex-1 truncate text-fg">{m.path}</span>
                      {(m.bus_factor === 1 || m.concentration >= 0.6) && <AlertTriangle className="h-3.5 w-3.5 text-warn" />}
                      <Owners owners={m.owners} />
                    </a>
                  ))}
                </div>
              )}
            </Card>
          ))}
        </div>
      )}
    </Page>
  )
}

function SystemView({ systemKey, theme }: { systemKey: string; theme: Theme }) {
  const { data, error, loading } = useAsync(() => api.system(systemKey), [systemKey])
  if (loading && !data) return <Page title={shortSystem(systemKey)}><Spinner /></Page>
  if (error || !data) return <Page title={shortSystem(systemKey)}><ErrorNote error={error ?? 'Not found'} /></Page>
  const team = data.impact.owning_teams?.[data.system.key]
  return (
    <Page
      title={shortSystem(data.system.key)}
      subtitle={[data.system.key, team && `owned by ${team}`].filter(Boolean).join(' · ')}
      actions={
        <>
          <a href={href('systems')} className="inline-flex items-center gap-1 text-sm text-muted hover:text-fg">
            <ArrowLeft className="h-4 w-4" /> Systems
          </a>
          <Button icon={<GitCompareArrows className="h-4 w-4" />} onClick={() => go('change', data.system.key)}>
            Plan a change
          </Button>
        </>
      }
    >
      <SectionTitle>Who knows it</SectionTitle>
      {data.experts.length === 0 ? (
        <p className="text-sm text-muted">No recorded activity.</p>
      ) : (
        <Card className="divide-y divide-line">
          {data.experts.map((e) => (
            <a key={e.handle} href={href('people', e.handle)} className="flex items-center gap-3 px-4 py-3 hover:bg-surface-2">
              <Avatar handle={e.handle} size={30} />
              <div className="min-w-0 flex-1">
                <p className="text-sm font-medium text-fg">
                  {e.name ?? e.handle} <span className="font-normal text-faint">{e.role ? `· ${e.role}` : ''}</span>
                </p>
                <p className="truncate text-xs text-muted">
                  {Object.entries(e.activity)
                    .map(([k, v]) => `${v} ${k}`)
                    .join(' · ')}
                  {e.last_active && ` · last active ${e.last_active}`}
                </p>
              </div>
              <span className="text-sm font-medium text-fg">{pct(e.share)}</span>
            </a>
          ))}
        </Card>
      )}

      <div className="mt-2 grid gap-6 sm:grid-cols-2">
        <div>
          <SectionTitle>Depends on</SectionTitle>
          {data.impact.depends_on.length === 0 ? (
            <p className="text-sm text-muted">Nothing recorded.</p>
          ) : (
            <ul className="space-y-1">
              {data.impact.depends_on.map((d) => (
                <li key={d.key}>
                  <a href={href('systems', d.key)} className="inline-flex items-center gap-1.5 text-sm text-fg hover:underline">
                    <ArrowUpRight className="h-3.5 w-3.5 text-faint" /> {shortSystem(d.key)}
                  </a>
                </li>
              ))}
            </ul>
          )}
        </div>
        <div>
          <SectionTitle>Depended on by</SectionTitle>
          {data.impact.depended_on_by.length === 0 ? (
            <p className="text-sm text-muted">Nothing recorded.</p>
          ) : (
            <ul className="space-y-1">
              {data.impact.depended_on_by.map((d) => (
                <li key={d.repo}>
                  <a href={href('systems', d.repo)} className="inline-flex items-center gap-1.5 text-sm text-fg hover:underline">
                    <ArrowDownLeft className="h-3.5 w-3.5 text-faint" /> {shortSystem(d.repo)}
                    {d.hops > 1 && <span className="text-xs text-faint">({d.hops} hops)</span>}
                  </a>
                </li>
              ))}
            </ul>
          )}
        </div>
      </div>

      {data.decisions.length > 0 && (
        <>
          <SectionTitle>Decisions</SectionTitle>
          <ul className="space-y-1.5">
            {data.decisions.map((d) => (
              <li key={d.uid} className="flex items-center gap-2 text-sm">
                {d.drift && <AlertTriangle className="h-3.5 w-3.5 text-danger" />}
                <a href={href('decisions', d.uid)} className="text-fg hover:underline">
                  {d.title}
                </a>
                <span className="text-xs text-faint">{d.date}</span>
              </li>
            ))}
          </ul>
        </>
      )}

      {data.timeline.length > 0 && (
        <>
          <SectionTitle>Recent activity</SectionTitle>
          <ul className="space-y-1.5">
            {data.timeline.map((e) => {
              const link = sourceHref(e.uid)
              return (
                <li key={e.uid} className="flex items-center gap-2.5 text-sm">
                  <span className="w-20 shrink-0 text-xs text-faint">{e.date}</span>
                  <Badge>{kindLabel(e.kind)}</Badge>
                  <a href={link.href} className="min-w-0 flex-1 truncate text-fg hover:underline">
                    {e.title}
                  </a>
                </li>
              )
            })}
          </ul>
        </>
      )}

      <SectionTitle>Graph</SectionTitle>
      <GraphView graph={data.graph} theme={theme} height={360} focus={`System:${data.system.key}`} />
    </Page>
  )
}
