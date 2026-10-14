export type NodeLabel = 'Person' | 'System' | 'Source' | 'Decision' | 'Premise' | 'Answer'

export interface GNode {
  id: string
  label: NodeLabel | string
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

export type AgentEvent =
  | { type: 'start'; question: string }
  | { type: 'notice'; message: string }
  | { type: 'tool_call'; id: string; name: string; args: Record<string, unknown> }
  | {
      type: 'tool_result'
      id: string
      name: string
      summary: string
      error?: boolean
      subgraph: SubGraph
      evidence: Citation[]
    }
  | { type: 'answer'; text: string; citations: Citation[]; answer_uid: string; mode: 'agent' | 'router' }
  | { type: 'error'; message: string }
  | { type: 'done'; usage: Record<string, Usage>; seconds: number }

export interface Usage {
  calls: number
  cache_hits: number
  prompt_tokens: number
  completion_tokens: number
  latency_ms: number
  errors: number
}

export interface Step {
  id: string
  name: string
  args: Record<string, unknown>
  summary?: string
  error?: boolean
  nodeIds: string[]
}

export interface Turn {
  id: string
  question: string
  steps: Step[]
  notices: string[]
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
}

export interface Stats {
  nodes: Record<string, number>
  relationships: Record<string, number>
  coverage: { sources?: number; processed?: number; newest?: number; oldest?: number }
  llm_usage: Record<string, Usage>
}

export interface ToolResponse<T = Record<string, unknown>> {
  summary: string
  data: T
  subgraph: SubGraph
  evidence: Citation[]
}
