import { useEffect, useMemo, useRef, useState } from 'react'
import {
  ACTION_LABEL,
  CONTEXT_REASON_LABEL,
  REASON_LABEL,
  RESOLUTION_LABEL,
  SIGNAL_LABEL,
  label,
} from './labels'
import type {
  BackgroundEvent,
  CaEvent,
  Cfg,
  PolicyEvent,
  Profile,
} from './types'
import { currentServer } from './servers'


type Row = {
  kind: 'info' | 'good' | 'deny' | 'warn' | 'nudge' | 'hold' | 'ghost' | 'ghostdeny'
  label: string
  line: string
  next?: string
  at: number
  raw?: CaEvent
  extra?: { k: string; v: string }[]
}

/** Analyzers emit every turn. Only transitions earn a row; the rest is
 *  heartbeat and lives in the downloadable record. */
const NOISE = new Set([
  'no_step', 'unchanged', 'no_intent', 'already_flagged', 'ungrounded', 'grounded',
])

function buildStream(events: CaEvent[], sopName: (id: string) => string): Row[] {
  const rows: Row[] = []
  const seenSteps = new Set<number>()

  for (const e of events) {
    const at = e._rx ?? Date.now()

    if (e.type === 'background') {
      const b = e as BackgroundEvent
      if (!NOISE.has(b.kind)) {
        if (b.kind === 'sop_identified')
          rows.push({
            kind: 'info', label: 'Procedure identified', line: sopName(b.detail), at, raw: e,
            extra: [{ k: 'id', v: b.detail }, { k: 'classifier', v: `${b.latency_ms}ms` }],
          })
        else if (b.kind === 'step_observed')
          rows.push({ kind: 'info', label: 'Step observed', line: b.detail, at, raw: e })
        else if (b.kind === 'social_engineering')
          rows.push({
            kind: 'warn', label: 'Risk signal', at, raw: e,
            line: label(SIGNAL_LABEL, b.detail),
            extra: [{ k: 'signal', v: b.detail }],
          })
      }
      if (b.nudge)
        rows.push({
          // Withheld is the interesting state: we found it, we wrote the nudge,
          // and the agent never saw it. Same row, visibly not delivered.
          kind: b.delivered === false ? 'ghost' : 'nudge',
          label: b.delivered === false ? 'Nudge withheld, gate is off' : 'Nudge sent to agent',
          line: b.note ?? b.nudge,
          next: b.next_step ?? undefined,
          at,
          raw: e,
        })
      continue
    }

    if (e.type === 'policy') {
      const p = e as PolicyEvent
      for (const n of p.steps_completed) {
        if (!seenSteps.has(n)) {
          seenSteps.add(n)
          rows.push({
            kind: 'good', label: 'Step completed',
            line: `Step ${n} of ${p.steps_total}, ${p.sop_name}`, at, raw: e,
          })
        }
      }
      const verdict: Record<string, { kind: Row['kind']; label: string }> = {
        allow: { kind: 'good', label: 'Allowed' },
        warn: { kind: 'warn', label: 'Allowed with a flag' },
        deny: { kind: 'deny', label: 'Blocked' },
        hold: { kind: 'hold', label: 'Held for analysis' },
      }
      let v = verdict[p.decision] ?? verdict.deny
      // Shadow mode. The decision is real and the action ran anyway, which is
      // the entire point of the row, so it must not read like a block.
      if (p.enforced === false && p.would_block)
        v = { kind: 'ghostdeny', label: 'Would have blocked' }
      rows.push({
        kind: v.kind,
        label: v.label,
        at,
        raw: e,
        line:
          p.decision === 'allow'
            ? label(ACTION_LABEL, p.action)
            : `${label(ACTION_LABEL, p.action)}: ${label(REASON_LABEL, p.reason)}`,
        next: p.decision === 'hold' ? `Agent says: “${p.say}”` : undefined,
        extra: [
          ...(p.control ? [{ k: 'rule', v: `${p.control} ${p.control_name}` }] : []),
          { k: 'code', v: p.reason },
          ...(p.resolution && p.resolution !== 'none'
            ? [{ k: 'resolution', v: label(RESOLUTION_LABEL, p.resolution) }]
            : []),
          {
            k: 'outcome',
            v: p.blocked
              ? 'action did not run'
              : p.enforced === false && p.would_block
                ? 'action ran anyway, gate is off'
                : 'action proceeded',
          },
          { k: 'checked in', v: `${p.latency_ms}ms` },
          ...(p.settle_ms ? [{ k: 'waited on analysis', v: `${p.settle_ms}ms` }] : []),
        ],
      })
      continue
    }

    if (e.type === 'enforcement') {
      rows.push({
        kind: e.on ? 'good' : 'ghost',
        label: e.on ? 'Gate connected' : 'Gate disconnected',
        line: e.on
          ? 'Findings reach the agent again.'
          : 'The agent keeps every rule in its prompt. Nothing it finds is reaching the agent.',
        at,
        raw: e,
      })
      continue
    }

    if (e.type === 'executed')
      rows.push({ kind: 'good', label: 'Done', line: e.detail, at, raw: e })
    // The other half of a block. A refusal that leaves the caller with nothing
    // is a bad call; a refusal that leaves them with a reference is the product.
    if (e.type === 'ticket')
      rows.push({
        kind: 'info', label: 'Ticket raised', at, raw: e,
        line: e.ticket,
        extra: [{ k: 'because', v: label(REASON_LABEL, e.reason) }],
      })
    if (e.type === 'escalated')
      rows.push({ kind: 'warn', label: 'Handed to a person', line: e.reason, at, raw: e })
  }
  return rows
}

function Expanded({ row }: { row: Row }) {
  const p = row.raw?.type === 'policy' ? (row.raw as PolicyEvent) : null
  return (
    <div className="exp">
      {row.extra?.map((x) => (
        <div key={x.k} className="expkv">
          <span>{x.k}</span>
          <span>{x.v}</span>
        </div>
      ))}

      {p && Object.keys(p.args).length > 0 && (
        <div className="expkv">
          <span>details</span>
          <span>
            {Object.entries(p.args)
              .filter(([, v]) => v !== null && v !== '')
              .map(([k, v]) => `${k}=${v}`)
              .join('  ')}
          </span>
        </div>
      )}

      {p && p.checks.length > 0 && (
        <>
          <div className="expttl">{p.checks.length} checks run, in order</div>
          <ul className="checks">
            {p.checks.map((c, i) => (
              <li key={i} className={c.passed ? 'ok' : 'bad'}>
                <span>{c.passed ? '✓' : '✕'}</span>
                <code>{c.control}</code>
                <span className="cn">{c.control_name}</span>
                <span className="detail">{c.detail}</span>
              </li>
            ))}
          </ul>
        </>
      )}

      {row.raw?.type === 'background' && (
        <div className="expkv">
          <span>analysis</span>
          <span>
            {(row.raw as BackgroundEvent).analyzer} · work{' '}
            {(row.raw as BackgroundEvent).latency_ms}ms · behind the call{' '}
            {(row.raw as BackgroundEvent).queue_lag_ms}ms
          </span>
        </div>
      )}
    </div>
  )
}

/** What the organisation gave the analysis to work with, or why it gave
 *  nothing. Rendered wherever a reader would otherwise assume the procedures on
 *  screen are theirs. */
export function ContextNote({ cfg }: { cfg: Cfg }) {
  // No reason means /sop has not answered yet. Saying anything at this point
  // would be reporting on a request that has not come back.
  if (!cfg.reason) return null

  const counts = [
    cfg.runbook ? 'a runbook' : null,
    cfg.sops.length ? `${cfg.sops.length} procedures` : null,
    cfg.controls.length ? `${cfg.controls.length} controls` : null,
    cfg.documents.length ? `${cfg.documents.length} documents` : null,
  ].filter(Boolean)

  if (counts.length)
    return (
      <p className="ctxnote">
        Working from {counts.join(', ')} for this organisation.
      </p>
    )

  return (
    <p className="ctxnote empty">
      No runbook, procedures or controls returned for this organisation.{' '}
      {label(CONTEXT_REASON_LABEL, cfg.reason)}
      {cfg.source && <code>GET {cfg.source}</code>}
    </p>
  )
}

export function Feed({
  events,
  cfg,
  room,
  profile,
}: {
  events: CaEvent[]
  cfg: Cfg
  room: string
  profile: Profile
}) {
  const streamRef = useRef<HTMLDivElement>(null)
  const atBottom = useRef(true)
  const [open, setOpen] = useState<Set<number>>(new Set())

  const sopName = useMemo(
    () => (id: string) => cfg.sops.find((s) => s.id === id)?.name ?? id,
    [cfg.sops],
  )
  const rows = useMemo(() => buildStream(events, sopName), [events, sopName])
  const t0 = rows.length ? rows[0].at : Date.now()

  useEffect(() => {
    const el = streamRef.current
    if (el && atBottom.current) el.scrollTop = el.scrollHeight
  }, [rows.length])

  const policies = events.filter((e): e is PolicyEvent => e.type === 'policy')
  const last = policies[policies.length - 1]
  const bg = events.filter((e): e is BackgroundEvent => e.type === 'background')
  const activeSop =
    [...bg].reverse().find((b) => b.kind === 'sop_identified')?.detail ?? last?.sop ?? null

  return (
    <aside className="panel">
      <header className="sidehead trustside">
        <div>
          <span className="wordmark">AgentTrust</span>
          <span className="byline">
            <span className="hex" aria-hidden="true">䷼</span> DeepTrust enforcement layer
          </span>
          <p className="sidesub">
            {profile.enforcement === false
              ? 'Watching every turn and finding exactly what it would. Nothing it finds is reaching the agent.'
              : 'Watching every turn, and telling the agent what it finds while the call is still happening.'}
          </p>
        </div>
      </header>

      <ContextNote cfg={cfg} />

      <div className="strip">
        <div>
          <span className="k">Procedure</span>
          <span className="v">{activeSop ? sopName(activeSop) : 'listening…'}</span>
        </div>
        <div>
          <span className="k">Caller</span>
          <span className="v pending" title="Nothing on this call has confirmed who the caller is">
            unverified
          </span>
        </div>
        <div>
          <span className="k">Progress</span>
          <span className="v">{last ? `step ${last.step} of ${last.steps_total}` : 'not yet'}</span>
        </div>
        <div>
          <span className="k">Last check</span>
          <span className="v mono">{last ? `${last.latency_ms}ms` : 'not yet'}</span>
        </div>
      </div>

      <div
        className="stream"
        ref={streamRef}
        onScroll={(e) => {
          const el = e.currentTarget
          atBottom.current = el.scrollHeight - el.scrollTop - el.clientHeight < 40
        }}
      >
        {rows.length === 0 && <p className="empty">Waiting for the first event.</p>}
        {rows.map((r, i) => {
          const isOpen = open.has(i)
          const canOpen = Boolean(r.extra?.length || r.raw?.type === 'policy')
          return (
            <div key={i} className={`row ${r.kind} ${isOpen ? 'open' : ''}`}>
              <button
                className="rowhead"
                disabled={!canOpen}
                aria-expanded={isOpen}
                onClick={() =>
                  setOpen((s) => {
                    const n = new Set(s)
                    n.has(i) ? n.delete(i) : n.add(i)
                    return n
                  })
                }
              >
                <span className="rt">{((r.at - t0) / 1000).toFixed(1)}s</span>
                <span className="rl">{r.label}</span>
                <span className="rline">{r.line}</span>
                {canOpen && <span className="chev">{isOpen ? '−' : '+'}</span>}
              </button>
              {r.next && (
                <p className="nextstep">
                  <span>Next</span>
                  {r.next}
                </p>
              )}
              {isOpen && <Expanded row={r} />}
            </div>
          )
        })}
      </div>

      <a className="dl" href={`${currentServer()}/record/${room}`} target="_blank" rel="noreferrer">
        Download the call record ↓
      </a>
    </aside>
  )
}
