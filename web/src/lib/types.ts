export interface GNode {
  id: string
  label: string
  props: Record<string, unknown>
}
export interface GEdge {
  id: string
  type: string
  source: string
  target: string
  props: Record<string, unknown>
}
export interface SubGraph {
  nodes: GNode[]
  edges: GEdge[]
}

export interface Citation {
  ref: number
  uid: string
  title: string
  url: string
  kind: string
  date: string | null
  quote?: string | null
}

export interface Usage {
  calls: number
  cache_hits: number
  prompt_tokens: number
  completion_tokens: number
  latency_ms: number
  errors: number
}

export interface Action {
  uid: string
  kind: string
  status: string
  title: string
  repo: string
  preview: string
  ts: number
  result_url: string | null
  error: string | null
  files: number | null
}

export type AgentEvent =
  | { type: 'start'; question: string }
  | { type: 'notice'; message: string }
  | { type: 'tool_call'; id: string; name: string; args: Record<string, unknown> }
  | { type: 'tool_result'; id: string; name: string; summary: string; error?: boolean; subgraph: SubGraph; evidence: Citation[]; action?: Action }
  | { type: 'answer'; text: string; citations: Citation[]; answer_uid: string; mode: 'agent' | 'router' }
  | { type: 'error'; message: string }
  | { type: 'done'; usage: Record<string, Usage>; seconds: number }

export interface Step {
  id: string
  name: string
  args: Record<string, unknown>
  summary?: string
  error?: boolean
  nodeIds: string[]
  action?: Action
}

export interface Turn {
  id: string
  question: string
  steps: Step[]
  notices: string[]
  graph: SubGraph
  answer?: { text: string; citations: Citation[]; mode: 'agent' | 'router' }
  error?: string
  seconds?: number
  pending: boolean
}

export interface Health {
  ok: boolean
  graph: string
  llm: Record<string, { model: string; ready: boolean }>
  repos: string[]
  admin_required: boolean
}

export interface WorkspaceInfo {
  graph: string
  name: string
  description: string
  people: number
  decisions: number
  sources: number
}

export interface WorkspaceSummary {
  people: number
  systems: number
  sources: number
  decisions: number
  reasons: number
  drifted: number
  pending_actions: number
  from: string | null
  to: string | null
  suggestions: string[]
}

export interface Stats {
  nodes: Record<string, number>
  relationships: Record<string, number>
  llm_usage: Record<string, Usage>
}

export interface DecisionItem {
  uid: string
  title: string
  summary: string
  category: string
  repo: string
  drift: boolean
  confidence: number
  url: string
  date: string | null
  deciders: string[]
  reasons: number
}

export interface Premise {
  claim: string
  quote: string
  source: string
  by: string | null
  challenged_by?: { source: string; date: string | null; verdict: string; quote: string; why: string }[]
  option?: string
}

export interface DecisionDetail {
  uid: string
  title: string
  summary: string
  category: string
  date: string | null
  status: string
  decided_in: string
  deciders: string[]
  systems: string[]
  premises: Premise[]
  rejected_alternatives: Premise[]
  drift_detected: boolean
  superseded_by: { uid: string; title: string }[]
  also_discussed_in: { uid: string; kind: string; title: string; date: string | null }[]
  grounding: number
  thread: { url: string; title: string } | null
  sources: SourceRef[]
  related: { uid: string; title: string; date: string | null }[]
}

export interface SourceRef {
  uid: string
  kind: string
  title: string
  url: string
  date: string | null
}

export interface PersonItem {
  handle: string
  name: string | null
  activity: number
  last_active: string | null
  areas: string[]
  decisions: number
}

export interface PersonProfile {
  person: { handle: string; name: string | null; role: string | null; team: string | null; manager: string | null; left_company: string | null }
  expertise: { system: string; share_of_knowledge: number; last_active: string | null }[]
  decisions: { uid: string; title: string; date: string | null }[]
  open_work: { uid: string; kind: string; title: string }[]
  would_leave_gap_in: string[]
}

export interface Owner {
  handle: string
  share: number
}

export interface ModuleItem {
  key: string
  path: string
  repo: string
  prs: number
  owners: Owner[]
  bus_factor: number
  concentration: number
  decisions: number
}

export interface SystemItem {
  key: string
  name: string
  description: string
  language: string | null
  url: string
  owners: Owner[]
  bus_factor: number
  concentration: number
  decisions: number
  depends_on: string[]
  depended_on_by: string[]
  modules: ModuleItem[]
}

export interface Expert {
  handle: string
  name: string | null
  role: string | null
  team: string | null
  left_company: string | null
  share: number
  activity: Record<string, number>
  last_active: string | null
  recent_work: { uid: string; title: string; kind: string; date: string | null }[]
}

export interface SystemDetail {
  system: { key: string; name: string; kind: string; repo: string }
  experts: Expert[]
  impact: {
    depended_on_by: { repo: string; hops: number }[]
    depends_on: { key: string; via: string }[]
    open_items: { uid: string; kind: string; title: string; date: string | null }[]
    owning_teams?: Record<string, string>
  }
  timeline: { uid: string; kind: string; title: string; date: string | null; state: string; author: string | null }[]
  decisions: { uid: string; title: string; date: string | null; drift: boolean }[]
  graph: SubGraph
}

export interface SourceDetail {
  uid: string
  kind: string
  title: string
  body: string
  state: string
  author: string | null
  labels: string[]
  repo: string
  date: string | null
  external_url: string | null
  parent: { uid: string; title: string; kind: string } | null
  replies: { uid: string; kind: string; author: string | null; body: string; date: string | null; external_url: string | null }[]
  decisions: { uid: string; title: string; date: string | null; drift: boolean; role: string }[]
  challenges: { uid: string; title: string; premise: string; quote: string }[]
  systems: string[]
  people: { handle: string; role: string }[]
}

export interface ToolResponse<D = Record<string, unknown>> {
  summary: string
  data: D
  subgraph: SubGraph
  evidence: Citation[]
}

export interface ChangeImpact {
  system: { key: string }
  change: string
  dependents: { repo: string; hops: number }[]
  constraints: { uid: string; title: string; date: string | null; reasons: string[]; drift_detected: boolean }[]
  open_items: { uid: string; kind: string; title: string; date: string | null }[]
  notify: { system: string; team: string | null; people: string[]; why: string }[]
  draft: string
}

export interface IncidentMemory {
  similar_incidents: {
    uid: string
    kind: string
    title: string
    date: string | null
    systems: string[]
    handled_by: { handle: string; left_company: string | null }[]
    what_fixed_it: string | null
  }[]
  systems: string[]
  runbooks: { uid: string; title: string; date: string | null; possibly_outdated: boolean; why: string[] }[]
  who_to_page: { handle: string; system: string; last_active: string | null }[]
  note: string | null
}

export interface HandoffPlan {
  person: PersonProfile['person']
  areas: { system: string; share_of_knowledge: number; successor: string | null; backup: string | null; read_first: { uid: string; title: string; kind: string }[] }[]
  decisions_to_reown: { uid: string; title: string; date: string | null }[]
  open_work: { uid: string; kind: string; title: string }[]
  draft: string
}

export interface SyncJob {
  id: string
  repos: string[]
  status: string
  stage: string | null
  detail: string
  started: number
  finished: number | null
  error: string | null
}

export interface DecisionGuard {
  title: string
  systems: string[]
  verdict: 'review' | 'heads_up' | 'clear'
  reverses: boolean
  findings: {
    uid: string
    title: string
    date: string | null
    level: 'conflict' | 'challenged' | 'respect'
    system: string
    deciders: string[]
    reasons: string[]
    drift_detected: boolean
    matched_terms: string[]
  }[]
  draft: string
}
