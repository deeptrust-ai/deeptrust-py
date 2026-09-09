export type Check = {
  control: string
  control_name: string
  step: number
  passed: boolean
  detail: string
}

export type PolicyEvent = Rx & {
  type: 'policy'
  decision: 'allow' | 'warn' | 'deny' | 'hold'
  /** false when the gate ran in shadow: it decided, and did not stop anything */
  enforced?: boolean
  /** shadow only: what blocked would have been */
  would_block?: boolean
  resolution?: string
  /** hold only: the line the agent was told to say while the gate settles */
  say?: string
  settle_ms?: number
  blocked: boolean
  warnings: { control: string; reason: string; message: string }[]
  control: string | null
  control_name: string | null
  sop: string
  sop_name: string
  step: number
  severity: string | null
  reason: string
  message: string | null
  action: string
  args: Record<string, unknown>
  fingerprint: string
  checks: Check[]
  steps_completed: number[]
  steps_total: number
  latency_ms: number
  phase: string
}

export type UtteranceEvent = Rx & {
  type: 'utterance'
  role: string
  text: string
  warning_signs: string[]
}

export type BackgroundEvent = Rx & {
  type: 'background'
  subtype: 'nudge' | 'finding'
  /** false when the gate was off: computed, shown, never sent to the agent */
  delivered?: boolean
  analyzer: string
  kind: string
  detail: string
  note: string | null
  next_step: string | null
  nudge: string | null
  latency_ms: number
  queue_lag_ms: number
  fanout_ms: number
  role: string
}

export type ExecutedEvent = Rx & { type: 'executed'; action: string; detail: string }
export type TicketEvent = Rx & {
  type: 'ticket'
  ticket: string
  reason: string
}
export type EscalatedEvent = Rx & { type: 'escalated'; reason: string }
export type EnforcementEvent = Rx & { type: 'enforcement'; on: boolean }
/** Which implementation of the semantic plane is running behind this call. */
export type EngineEvent = Rx & { type: 'engine'; analysis: 'mock' | 'sdk' }

/** Stamped by the client when the packet arrives. */
export type Rx = { _rx?: number }

export type CaEvent =
  | PolicyEvent
  | UtteranceEvent
  | BackgroundEvent
  | ExecutedEvent
  | TicketEvent
  | EscalatedEvent
  | EnforcementEvent
  | EngineEvent

/** Who the caller says they are. Nothing on the call confirms any of it: the
 *  name and the role are a claim the browser made, and the panel and the call
 *  record both show them as one. */
export type Profile = {
  name: string
  username: string
  role: string
  enforcement?: boolean
}

export type Sop = {
  id: string
  name: string
  description: string
  scope: string
  procedureSteps: string[]
  forbiddenActions: string[]
  warningSigns: string[]
}

export type Runbook = {
  about: string
  normalActivity: string
  verificationCulture: string
  escalation: string
  teamContext: { team: string; content: string }[]
  definitions: { term: string; definition: string }[]
}

export type KbDocument = {
  id: string
  title: string
  owner: string
  updated: string
  summary: string
}

export type Predicate = {
  field: string
  op: string
  value?: unknown
  unless_field_in?: [string, unknown[]]
}

export type Control = {
  id: string
  name: string
  sop: string
  step: number
  actions: string[]
  severity: 'critical' | 'high' | 'medium'
  phase?: string
  require: Predicate[]
  reason: string
  message: string
}

/** The organisation's own runbook, procedures and controls, fetched once from
 *  GET /sop, which reads them from the DeepTrust API. Lives here rather than in
 *  App.tsx because the call shell and both platform containers all take it.
 *
 *  Every list can legitimately be empty, so `reason` says why: an organisation
 *  nobody has configured yet and a request the API refused look identical
 *  otherwise, and neither may be dressed up as policy. `source` is the URL that
 *  was asked, which is the first thing anyone wants when the answer is empty. */
export type Cfg = {
  runbook: Runbook | null
  sops: Sop[]
  documents: KbDocument[]
  controls: Control[]
  protectedAccounts: string[]
  reason: string
  source: string
}
