import type * as T from './types'

const BASE = import.meta.env.VITE_API_BASE ?? ''
const WS_KEY = 'cairn.workspace'
const TOKEN_KEY = 'cairn.adminToken'

function read(key: string): string | null {
  try {
    return localStorage.getItem(key)
  } catch {
    return null
  }
}

function write(key: string, value: string | null) {
  try {
    if (value) localStorage.setItem(key, value)
    else localStorage.removeItem(key)
  } catch {
    /* private mode: keep in memory only */
  }
}

let workspace = read(WS_KEY)
let adminToken = read(TOKEN_KEY)

export const session = {
  get workspace() {
    return workspace
  },
  setWorkspace(graph: string | null) {
    workspace = graph
    write(WS_KEY, graph)
  },
  get adminToken() {
    return adminToken
  },
  setAdminToken(token: string | null) {
    adminToken = token
    write(TOKEN_KEY, token)
  },
}

export class ApiError extends Error {
  status: number
  constructor(status: number, message: string) {
    super(message)
    this.status = status
  }
}

function headers(extra?: Record<string, string>): Record<string, string> {
  const h: Record<string, string> = { ...extra }
  if (workspace) h['X-Cairn-Workspace'] = workspace
  if (adminToken) h.Authorization = `Bearer ${adminToken}`
  return h
}

async function json<R>(path: string, init?: RequestInit): Promise<R> {
  const res = await fetch(`${BASE}${path}`, { ...init, headers: headers(init?.headers as Record<string, string>) })
  if (!res.ok) {
    let detail = res.statusText
    try {
      const body = await res.json()
      detail = typeof body.detail === 'string' ? body.detail : JSON.stringify(body.detail)
    } catch {
      /* non-JSON error body */
    }
    throw new ApiError(res.status, detail)
  }
  return res.json() as Promise<R>
}

const post = (body: unknown): RequestInit => ({ method: 'POST', headers: { 'content-type': 'application/json' }, body: JSON.stringify(body) })
const q = (params: Record<string, string | number | boolean | undefined | null>) => {
  const s = new URLSearchParams()
  for (const [k, v] of Object.entries(params)) if (v !== undefined && v !== null && v !== '' && v !== false) s.set(k, String(v))
  const str = s.toString()
  return str ? `?${str}` : ''
}
const enc = (s: string) => s.split('/').map(encodeURIComponent).join('/')

export const api = {
  health: () => json<T.Health>('/api/health'),
  workspaces: () => json<{ items: T.WorkspaceInfo[]; default: string }>('/api/workspaces'),
  workspace: () => json<T.WorkspaceSummary>('/api/workspace'),
  stats: () => json<T.Stats>('/api/stats'),
  decisions: (params: { q?: string; drift?: boolean; limit?: number; offset?: number }) =>
    json<{ items: T.DecisionItem[]; total: number }>(`/api/decisions${q(params)}`),
  decision: (uid: string) => json<{ decision: T.DecisionDetail; graph: T.SubGraph }>(`/api/decisions/${enc(uid)}`),
  people: (search?: string) => json<{ items: T.PersonItem[] }>(`/api/people${q({ q: search, limit: 120 })}`),
  person: (handle: string) => json<{ person: T.PersonProfile; sources: T.SourceRef[]; graph: T.SubGraph }>(`/api/people/${encodeURIComponent(handle)}`),
  systems: () => json<{ items: T.SystemItem[] }>('/api/systems'),
  system: (key: string) => json<T.SystemDetail>(`/api/systems/${enc(key)}`),
  source: (uid: string) => json<T.SourceDetail>(`/api/sources/${enc(uid)}`),
  tool: <R = Record<string, unknown>>(name: string, args: Record<string, unknown> = {}) =>
    json<T.ToolResponse<R>>(`/api/tools/${name}`, post({ args })),
  changeImpact: (system: string, change: string) => json<T.ToolResponse<T.ChangeImpact>>('/api/workflows/change-impact', post({ system, change })),
  incident: (description: string) => json<T.ToolResponse<T.IncidentMemory>>('/api/workflows/incident', post({ description })),
  handoff: (handle: string) => json<T.ToolResponse<T.HandoffPlan>>('/api/workflows/handoff', post({ handle })),
  proposeChange: (system: string, change: string) => json<T.Action>('/api/workflows/change-impact/propose', post({ system, change })),
  proposeHandoff: (handle: string) => json<T.Action>('/api/workflows/handoff/propose', post({ handle })),
  actions: () => json<{ actions: T.Action[]; allowed_repos: string[]; wiki_repo: string | null }>('/api/actions'),
  proposeWiki: (reason: string) => json<T.Action>('/api/actions/wiki', post({ reason })),
  proposeRoute: (repo: string, number: number) => json<T.Action>('/api/actions/route-issue', post({ repo, number })),
  approve: (uid: string) => json<T.Action>(`/api/actions/${uid}/approve`, post({})),
  reject: (uid: string) => json<T.Action>(`/api/actions/${uid}/reject`, post({})),
  sync: () => json<{ job: T.SyncJob | null; repos: string[]; github_ready: boolean }>('/api/sync'),
  startSync: (repos: string[]) => json<{ job: T.SyncJob }>('/api/sync', post({ repos })),
}

/** Stream agent events. EventSource only supports GET, so the SSE body of a POST is read manually. */
export async function ask(
  question: string,
  history: { role: string; content: string }[],
  onEvent: (e: T.AgentEvent) => void,
  signal: AbortSignal,
): Promise<void> {
  const res = await fetch(`${BASE}/api/ask`, {
    method: 'POST',
    headers: headers({ 'content-type': 'application/json' }),
    body: JSON.stringify({ question, history }),
    signal,
  })
  if (!res.ok || !res.body) {
    let detail = `${res.status} ${res.statusText}`
    try {
      detail = (await res.json()).detail ?? detail
    } catch {
      /* ignore */
    }
    throw new ApiError(res.status, detail)
  }
  const reader = res.body.pipeThrough(new TextDecoderStream()).getReader()
  let buffer = ''
  for (;;) {
    const { value, done } = await reader.read()
    if (done) break
    buffer += value
    let sep: number
    while ((sep = buffer.indexOf('\n\n')) !== -1) {
      const frame = buffer.slice(0, sep)
      buffer = buffer.slice(sep + 2)
      const data = frame
        .split('\n')
        .filter((l) => l.startsWith('data:'))
        .map((l) => l.slice(5).trimStart())
        .join('\n')
      if (data) onEvent(JSON.parse(data) as T.AgentEvent)
    }
  }
}
