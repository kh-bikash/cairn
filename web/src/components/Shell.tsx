import { Boxes, GitCompareArrows, Inbox, Landmark, Menu, MessageSquareText, Moon, Settings, Siren, Sun, UserRoundMinus, Users, X } from 'lucide-react'
import { useState, type ReactNode } from 'react'
import { href, type Route } from '../lib/router'
import type { Theme } from '../lib/theme'
import type { Health, WorkspaceInfo } from '../lib/types'
import { cx } from './ui'

interface Props {
  route: Route
  theme: Theme
  onToggleTheme: () => void
  health: Health | null
  healthError: boolean
  workspaces: WorkspaceInfo[]
  workspace: string | null
  onWorkspace: (graph: string) => void
  pending: number
  children: ReactNode
}

const NAV: { page: string; label: string; icon: typeof Users; group?: string }[] = [
  { page: 'ask', label: 'Ask', icon: MessageSquareText },
  { page: 'decisions', label: 'Decisions', icon: Landmark },
  { page: 'people', label: 'People', icon: Users },
  { page: 'systems', label: 'Systems', icon: Boxes },
  { page: 'change', label: 'Change impact', icon: GitCompareArrows, group: 'Workflows' },
  { page: 'incident', label: 'Incident memory', icon: Siren, group: 'Workflows' },
  { page: 'handoff', label: 'Handoff', icon: UserRoundMinus, group: 'Workflows' },
  { page: 'approvals', label: 'Approvals', icon: Inbox, group: 'Workspace' },
  { page: 'settings', label: 'Settings', icon: Settings, group: 'Workspace' },
]

export function Shell({ route, theme, onToggleTheme, health, healthError, workspaces, workspace, onWorkspace, pending, children }: Props) {
  const [open, setOpen] = useState(false)
  const agentReady = health?.llm.agent?.ready
  const model = health?.llm.agent?.model.split(',')[0].split(':').slice(1).join(':')
  const current = route.page === 'source' ? '' : route.page

  const sidebar = (
    <nav className="flex h-full flex-col gap-1 p-3" aria-label="Main">
      <a href={href('ask')} className="mb-3 flex items-center gap-2.5 px-2 py-1.5" onClick={() => setOpen(false)}>
        <img src="/cairn.svg" alt="" className="h-7 w-7" />
        <span className="text-[15px] font-semibold tracking-tight text-fg">Cairn</span>
      </a>
      <label className="mb-3 block px-1">
        <span className="mb-1 block px-1 text-[10.5px] font-semibold uppercase tracking-wider text-faint">Workspace</span>
        <select
          value={workspace ?? ''}
          onChange={(e) => onWorkspace(e.target.value)}
          className="h-9 w-full rounded-lg border border-line bg-surface px-2 text-sm text-fg focus:border-accent focus:outline-none"
          aria-label="Workspace"
        >
          {workspaces.map((w) => (
            <option key={w.graph} value={w.graph}>
              {w.name}
            </option>
          ))}
        </select>
      </label>
      {NAV.map((item, i) => {
        const Icon = item.icon
        const showGroup = item.group && item.group !== NAV[i - 1]?.group
        return (
          <div key={item.page}>
            {showGroup && <p className="mb-1 mt-4 px-3 text-[10.5px] font-semibold uppercase tracking-wider text-faint">{item.group}</p>}
            <a
              href={href(item.page)}
              onClick={() => setOpen(false)}
              aria-current={current === item.page ? 'page' : undefined}
              className={cx(
                'flex items-center gap-2.5 rounded-lg px-3 py-2 text-sm transition',
                current === item.page ? 'bg-surface-2 font-medium text-fg' : 'text-muted hover:bg-surface-2 hover:text-fg',
              )}
            >
              <Icon className="h-4 w-4" />
              <span className="flex-1">{item.label}</span>
              {item.page === 'approvals' && pending > 0 && (
                <span className="rounded-full bg-accent px-1.5 text-[10.5px] font-semibold text-accent-fg">{pending}</span>
              )}
            </a>
          </div>
        )
      })}
      <div className="mt-auto flex items-center gap-2 border-t border-line px-2 pt-3">
        <span className={cx('h-2 w-2 shrink-0 rounded-full', healthError ? 'bg-danger' : agentReady ? 'bg-ok' : 'bg-warn')} />
        <span className="min-w-0 flex-1 truncate text-xs text-muted" title={healthError ? 'API unreachable' : agentReady ? model : 'No AI key: answers come straight from the graph'}>
          {healthError ? 'Reconnecting…' : agentReady ? model : 'Graph-only answers'}
        </span>
        <button onClick={onToggleTheme} className="rounded-md p-1.5 text-muted hover:bg-surface-2 hover:text-fg" aria-label="Toggle theme">
          {theme === 'dark' ? <Sun className="h-4 w-4" /> : <Moon className="h-4 w-4" />}
        </button>
      </div>
    </nav>
  )

  return (
    <div className="flex h-dvh bg-bg text-fg">
      <aside className="hidden w-60 shrink-0 border-r border-line bg-surface lg:block">{sidebar}</aside>
      {open && (
        <div className="fixed inset-0 z-40 lg:hidden">
          <button className="absolute inset-0 bg-black/30" aria-label="Close menu" onClick={() => setOpen(false)} />
          <aside className="absolute inset-y-0 left-0 w-64 border-r border-line bg-surface shadow-xl">{sidebar}</aside>
        </div>
      )}
      <div className="flex min-w-0 flex-1 flex-col">
        <header className="flex items-center gap-2 border-b border-line bg-surface px-4 py-2.5 lg:hidden">
          <button onClick={() => setOpen((o) => !o)} className="rounded-md p-1.5 text-muted hover:bg-surface-2" aria-label="Menu">
            {open ? <X className="h-5 w-5" /> : <Menu className="h-5 w-5" />}
          </button>
          <img src="/cairn.svg" alt="" className="h-6 w-6" />
          <span className="font-semibold">Cairn</span>
        </header>
        <main className="min-h-0 flex-1 overflow-y-auto scroll-thin">{children}</main>
      </div>
    </div>
  )
}
