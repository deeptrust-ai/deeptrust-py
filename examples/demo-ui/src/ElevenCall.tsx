/** The ElevenLabs path.
 *
 *  On LiveKit the tools live inside our worker, so the gate is a function call
 *  away. Here ElevenLabs holds the conversation and the browser holds the
 *  tools, so each tool call is relayed to POST /tool on our backend, which runs
 *  the same HelpDesk bodies the worker runs and calls the same gate. The policy
 *  and the tool logic exist once. Only the transport changes.
 *
 *  In production this would be a *server* tool, a webhook straight from
 *  ElevenLabs to a URL the customer owns, with the gate inline in that handler.
 *  Client tools are used here because a server tool needs a publicly reachable
 *  URL and a demo has to run on a laptop.
 */
import { useCallback, useEffect, useRef, useState } from 'react'
import { Conversation } from '@elevenlabs/client'
import { CallShell } from './CallShell'
import type { CaEvent, Cfg, Profile } from './types'
import { serverFor } from './servers'

/** Prefixed so the ElevenLabs lifecycle can be filtered out of a noisy console. */
const log = (...args: unknown[]) => console.log('[DT:eleven]', ...args)


const CA = serverFor('elevenlabs')
const BACKEND = serverFor('elevenlabs')

export type ElevenConn = {
  platform: 'elevenlabs'
  token: string
  agent_id: string
  profile: Profile
  dynamic_variables: Record<string, string>
  /**
   * The input the mic check settled on. The SDK opens its own capture, so
   * without this it takes the system default, which on a machine with any
   * meeting app installed is a virtual device that returns silence.
   */
  deviceId?: string
}

export function ElevenCall({
  conn,
  cfg,
  onLeave,
}: {
  conn: ElevenConn
  cfg: Cfg
  onLeave: () => void
}) {
  const [events, setEvents] = useState<CaEvent[]>([])
  const [mode, setMode] = useState('connecting')
  const [muted, setMuted] = useState(false)
  const [gateOn, setGateOn] = useState(conn.profile.enforcement !== false)
  const [sessionId, setSessionId] = useState('')

  const convo = useRef<Awaited<ReturnType<typeof Conversation.startSession>> | null>(null)
  const started = useRef(false)
  // Read inside the tool relay and the nudge stream, both of which outlive any
  // one render, so they need the current value rather than a captured one.
  const gateRef = useRef(gateOn)
  gateRef.current = gateOn
  const sidRef = useRef('')

  const emit = useCallback((e: Record<string, unknown>) => {
    setEvents((prev) => [...prev, { ...e, _rx: Date.now() } as CaEvent])
  }, [])

  /** A caller turn: render it, and give it to the semantic plane. */
  const callerTurn = useCallback(
    async (text: string) => {
      let signs: string[] = []
      try {
        const r = await fetch(`${CA}/utterance`, {
          method: 'POST',
          headers: { 'content-type': 'application/json' },
          body: JSON.stringify({ session_id: sidRef.current, role: 'user', text }),
        })
        signs = (await r.json()).warning_signs ?? []
      } catch {
        /* the transcript still renders */
      }
      emit({ type: 'utterance', role: 'user', text, warning_signs: signs })
    },
    [emit],
  )

  // ── the tool relay, which is where the gate sits ─────────────────────────
  const runTool = useCallback(
    async (name: string, args: Record<string, unknown>): Promise<string> => {
      try {
        const r = await fetch(`${BACKEND}/tool`, {
          method: 'POST',
          headers: { 'content-type': 'application/json' },
          body: JSON.stringify({
            session_id: sidRef.current || 'pending',
            profile: conn.profile,
            name,
            args,
            enforced: gateRef.current,
          }),
        })
        const d = await r.json()
        for (const ev of d.events ?? []) emit(ev)
        return d.result ?? ''
      } catch (err) {
        // The agent must never be told an action succeeded when we could not
        // reach the gate. Fail closed, in words the model will act on.
        return `Refused: the policy layer is unreachable, so nothing was done. ${err}`
      }
    },
    [conn.profile, emit],
  )

  const tool = useCallback(
    (name: string) => async (params: Record<string, unknown>) =>
      runTool(name, params ?? {}),
    [runTool],
  )

  // ── start the conversation once ──────────────────────────────────────────
  useEffect(() => {
    if (started.current) return
    started.current = true

    let cancelled = false
    void (async () => {
      try {
        // The SDK owns mic capture, so when audio does not arrive there is no
        // way to tell from the UI whether permission was refused, the device is
        // silent, or the transport never came up. Ask first and say which.
        try {
          const probe = await navigator.mediaDevices.getUserMedia({ audio: true })
          const track = probe.getAudioTracks()[0]
          log('mic granted', {
            label: track?.label,
            enabled: track?.enabled,
            muted: track?.muted,
            readyState: track?.readyState,
          })
          // Released immediately: the SDK opens its own capture, and holding
          // this one can leave the device busy.
          probe.getTracks().forEach((t) => t.stop())
        } catch (err) {
          log('mic REFUSED or unavailable', String(err))
          emit({ type: 'analyzer_error', detail: `Microphone unavailable: ${String(err)}` })
        }

        log('startSession', { hasToken: Boolean(conn.token), agent: conn.agent_id })
        const c = await Conversation.startSession({
          conversationToken: conn.token,
          dynamicVariables: conn.dynamic_variables,
          ...(conn.deviceId ? { inputDeviceId: conn.deviceId } : {}),
          clientTools: {
            propose_action: tool('propose_action'),
            apply_action: tool('apply_action'),
            open_ticket: tool('open_ticket'),
            transfer_to_human: tool('transfer_to_human'),
          },
          onConnect: ({ conversationId }) => {
            log('connected', conversationId)
            sidRef.current = conversationId
            setSessionId(conversationId)
          },
          onModeChange: ({ mode: m }) => {
            log('mode', m)
            setMode(m === 'speaking' ? 'speaking' : 'listening')
          },
          onStatusChange: ({ status }) => {
            log('status', status)
            if (status === 'disconnected') setMode('ended')
          },
          onMessage: ({ message, source }) => {
            log('message', source, JSON.stringify(message).slice(0, 90))
            const role = source === 'user' ? 'user' : 'assistant'
            if (role === 'assistant') {
              emit({ type: 'utterance', role, text: message, warning_signs: [] })
              return
            }
            // A typed message comes back through here as "...", so the plane
            // was being fed ellipses. Typed turns are handled where they are
            // sent; this path is for real speech.
            const t = message.trim()
            if (!t || t === '...') return
            void callerTurn(t)
          },
          onError: (m) => {
            log('ERROR', m)
            emit({ type: 'analyzer_error', detail: m })
          },
        })
        if (cancelled) {
          await c.endSession()
          return
        }
        convo.current = c
        sidRef.current = c.getId()
        setSessionId(c.getId())
        log('session live', c.getId())
        // Input volume is the one number that separates "no permission" from
        // "permission granted and the device is silent".
        const meter = setInterval(() => {
          try {
            log('input volume', c.getInputVolume?.().toFixed(3))
          } catch {
            // Older client builds do not expose it; the rest of the log stands.
          }
        }, 3000)
        window.setTimeout(() => clearInterval(meter), 30000)
      } catch (err) {
        log('startSession THREW', err)
        emit({ type: 'analyzer_error', detail: String(err) })
      }
    })()

    return () => {
      cancelled = true
      void convo.current?.endSession()
      convo.current = null
    }
  }, [conn.token, conn.dynamic_variables, tool, emit, callerTurn])

  // ── the semantic plane's back channel ────────────────────────────────────
  useEffect(() => {
    if (!sessionId) return
    const es = new EventSource(`${CA}/nudges/${sessionId}`)
    es.onmessage = (m) => {
      let ev: Record<string, unknown>
      try {
        ev = JSON.parse(m.data)
      } catch {
        return
      }
      const delivered = gateRef.current
      emit({ ...ev, type: 'background', subtype: ev.type, delivered })
      if (delivered && ev.type === 'nudge' && ev.nudge) {
        // Contextual updates are documented as non-interrupting, so unlike the
        // LiveKit path this shapes the next turn rather than the current one.
        convo.current?.sendContextualUpdate(String(ev.nudge))
      }
    }
    return () => es.close()
  }, [sessionId, emit])

  const setGate = useCallback(
    (on: boolean) => {
      setGateOn(on)
      gateRef.current = on
      emit({ type: 'enforcement', on })
    },
    [emit],
  )

  return (
    <CallShell
      profile={conn.profile}
      platform="ElevenLabs"
      platformNote="Running on the ElevenLabs Agents platform. Their agent, their model, their voice. Every action it takes is relayed to our gate before it runs."
      events={events}
      cfg={cfg}
      gateOn={gateOn}
      gateLabel={gateOn ? 'Gate on' : 'Gate off'}
      onGate={setGate}
      onLeave={onLeave}
      onSend={(t) => {
        convo.current?.sendUserMessage(t)
        void callerTurn(t)
      }}
      sessionId={sessionId}
      viz={
        <>
          <div className={`elbars ${mode}`} aria-hidden="true">
            {Array.from({ length: 7 }).map((_, i) => (
              <span key={i} style={{ animationDelay: `${i * 90}ms` }} />
            ))}
          </div>
          <span className="state">{mode}</span>
        </>
      }
      micControls={
        <>
          <span className={`micdot ${muted ? 'off' : 'on'}`} />
          <span className="miclabel">{muted ? 'mic muted' : 'mic live'}</span>
          <button
            className="ghost"
            onClick={() => {
              const next = !muted
              convo.current?.setMicMuted(next)
              setMuted(next)
            }}
          >
            {muted ? 'Unmute' : 'Mute'}
          </button>
        </>
      }
    />
  )
}
