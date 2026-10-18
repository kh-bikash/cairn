import { useCallback, useEffect, useState } from 'react'
import { ErrorBoundary } from './components/ErrorBoundary'
import { Shell } from './components/Shell'
import { api, session } from './lib/api'
import { useRoute } from './lib/router'
import { useTheme } from './lib/theme'
import type { Health, WorkspaceInfo, WorkspaceSummary } from './lib/types'
import { ApprovalsPage } from './pages/Approvals'
import { AskPage } from './pages/Ask'
import { DecisionsPage } from './pages/Decisions'
import { PeoplePage } from './pages/People'
import { SettingsPage } from './pages/Settings'
import { SourcePage } from './pages/Source'
import { SystemsPage } from './pages/Systems'
import { ChangePage, GuardPage, HandoffPage, IncidentPage } from './pages/Workflows'

export default function App() {
  const route = useRoute()
  const [theme, toggleTheme] = useTheme()
  const [health, setHealth] = useState<Health | null>(null)
  const [healthError, setHealthError] = useState(false)
  const [workspaces, setWorkspaces] = useState<WorkspaceInfo[]>([])
  const [workspace, setWorkspace] = useState<string | null>(session.workspace)
  const [summary, setSummary] = useState<WorkspaceSummary | null>(null)

  // Health: retry quickly while the API is unreachable, then refresh every 30s.
  useEffect(() => {
    let timer: ReturnType<typeof setTimeout> | undefined
    let cancelled = false
    const poll = async () => {
      try {
        const h = await api.health()
        if (cancelled) return
        setHealth(h)
        setHealthError(false)
        timer = setTimeout(poll, 30_000)
      } catch {
        if (cancelled) return
        setHealthError(true)
        timer = setTimeout(poll, 3_000)
      }
    }
    void poll()
    return () => {
      cancelled = true
      clearTimeout(timer)
    }
  }, [])

  useEffect(() => {
    if (!health) return
    api
      .workspaces()
      .then((r) => {
        setWorkspaces(r.items)
        // Fall back to the default graph if the remembered workspace no longer exists.
        if (!workspace || !r.items.some((w) => w.graph === workspace)) {
          session.setWorkspace(r.default)
          setWorkspace(r.default)
        }
      })
      .catch(() => setWorkspaces([]))
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [health?.ok])

  const refreshSummary = useCallback(() => {
    api.workspace().then(setSummary).catch(() => setSummary(null))
  }, [])
  useEffect(() => {
    if (workspace) refreshSummary()
  }, [workspace, refreshSummary])

  const onWorkspace = (graph: string) => {
    session.setWorkspace(graph)
    setWorkspace(graph)
    setSummary(null)
    window.location.hash = '#/ask'
  }

  const current = workspaces.find((w) => w.graph === workspace)
  const page = (() => {
    switch (route.page) {
      case 'decisions':
        return <DecisionsPage id={route.id} theme={theme} />
      case 'people':
        return <PeoplePage id={route.id} theme={theme} />
      case 'systems':
        return <SystemsPage id={route.id} theme={theme} />
      case 'source':
        return route.id ? <SourcePage uid={route.id} /> : null
      case 'guard':
        return <GuardPage theme={theme} />
      case 'change':
        return <ChangePage initial={route.id} theme={theme} />
      case 'incident':
        return <IncidentPage theme={theme} />
      case 'handoff':
        return <HandoffPage initial={route.id} theme={theme} />
      case 'approvals':
        return <ApprovalsPage onChanged={refreshSummary} />
      case 'settings':
        return <SettingsPage health={health} workspace={current} />
      default:
        return <AskPage summary={summary} workspaceName={current?.name ?? 'Your company'} theme={theme} />
    }
  })()

  return (
    <Shell
      route={route}
      theme={theme}
      onToggleTheme={toggleTheme}
      health={health}
      healthError={healthError}
      workspaces={workspaces}
      workspace={workspace}
      onWorkspace={onWorkspace}
      pending={summary?.pending_actions ?? 0}
    >
      {/* Keyed by workspace + route so switching workspace or page never shows stale data. */}
      <ErrorBoundary key={`${workspace}|${route.page}|${route.id}`} name="page">
        {workspace ? page : null}
      </ErrorBoundary>
    </Shell>
  )
}
