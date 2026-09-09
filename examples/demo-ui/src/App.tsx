import {
  BarVisualizer,
  LiveKitRoom,
  RoomAudioRenderer,
  StartAudio,
  useDataChannel,
  useLocalParticipant,
  useVoiceAssistant,
} from '@livekit/components-react'
import { useCallback, useEffect, useRef, useState } from 'react'
import { MicCheck } from './MicCheck'
import { CallShell } from './CallShell'
import { ContextNote } from './Panel'
import { ElevenCall, type ElevenConn } from './ElevenCall'
import type { CaEvent, Cfg, Profile } from './types'
import { serverFor, setCurrentServer } from './servers'

type LiveKitConn = {
  platform: 'livekit'
  token: string
  url: string
  room: string
  profile: Profile
}
type Conn = LiveKitConn | ElevenConn

type ModelOption = {
  id: string
  label: string
  provider: string
  reasoning: boolean
  ms: number
  note: string
  available: boolean
}

// What the UI shows before /sop has answered. `reason` is blank rather than a
// code, because nothing has been asked yet and every code means something did.
const EMPTY_CFG: Cfg = {
  runbook: null, sops: [], documents: [], controls: [], protectedAccounts: [],
  reason: '', source: '',
}

const IconPhone = () => (
  <svg viewBox="0 0 16 16" width="14" height="14" aria-hidden="true">
    <path
      d="M3.2 1.8h2.1l1 2.6-1.3 1a8.4 8.4 0 0 0 3.6 3.6l1-1.3 2.6 1v2.1c0 .6-.5 1.1-1.1 1A11.6 11.6 0 0 1 2.2 2.9c-.1-.6.4-1.1 1-1.1Z"
      fill="none" stroke="currentColor" strokeWidth="1.3" strokeLinejoin="round"
    />
  </svg>
)

const IconPlay = () => (
  <svg viewBox="0 0 16 16" width="14" height="14" aria-hidden="true">
    <circle cx="8" cy="8" r="6.2" fill="none" stroke="currentColor" strokeWidth="1.3" />
    <path d="M6.6 5.6l4 2.4-4 2.4V5.6Z" fill="currentColor" />
  </svg>
)

const IconBook = () => (
  <svg viewBox="0 0 16 16" width="14" height="14" aria-hidden="true">
    <path
      d="M2.2 2.6h4a2 2 0 0 1 1.8 1.1 2 2 0 0 1 1.8-1.1h4v9.4h-4a2 2 0 0 0-1.8 1.1 2 2 0 0 0-1.8-1.1h-4V2.6ZM8 3.7v9.4"
      fill="none" stroke="currentColor" strokeWidth="1.3" strokeLinejoin="round"
    />
  </svg>
)

function Lobby({
  onJoin,
  deviceId,
  onDevice,
  cfg,
}: {
  onJoin: (c: Conn) => void
  deviceId: string
  onDevice: (id: string) => void
  cfg: Cfg
}) {
  const [micOk, setMicOk] = useState(false)
  const [models, setModels] = useState<ModelOption[]>([])
  const [llm, setLlm] = useState('')
  const [enforcement, setEnforcement] = useState(true)
  const [platform, setPlatform] = useState<'livekit' | 'elevenlabs'>('livekit')

  const [roles, setRoles] = useState<string[]>([])
  const [name, setName] = useState('')
  const [role, setRole] = useState('MEMBER')
  const [busy, setBusy] = useState(false)

  useEffect(() => {
    fetch(`${serverFor(platform)}/profiles`)
      .then((r) => r.json())
      .then((d) => {
        setRoles(d.roles)
        setRole((current) => (d.roles?.includes(current) ? current : d.roles?.[0] ?? current))
        setModels(d.models)
        setLlm(
          d.models.find((m: ModelOption) => m.id === d.default_model)?.available
            ? d.default_model
            : (d.models.find((m: ModelOption) => m.available)?.id ?? ''),
        )
      })
      .catch(() => {})
  }, [])

  const join = useCallback(
    async (body: Record<string, unknown>) => {
      setBusy(true)
      try {
        const path = platform === 'elevenlabs' ? '/eleven/session' : '/token'
        const backend = serverFor(platform)
        setCurrentServer(platform)
        console.log('[DT:join]', { platform, path, backend })
        const r = await fetch(`${backend}${path}`, {
          method: 'POST',
          headers: { 'content-type': 'application/json' },
          body: JSON.stringify({ ...body, llm, enforcement }),
        })
        const payload = await r.json()
        console.log('[DT:join] response', r.status, Object.keys(payload))
        // `platform` last so the selection decides which call surface renders,
        // whatever the server happens to echo back. `deviceId` rides along
        // because the ElevenLabs SDK opens its own capture.
        onJoin({ ...payload, platform, deviceId })
      } finally {
        setBusy(false)
      }
    },
    [onJoin, llm, enforcement, platform, deviceId],
  )

  const chosen = models.find((m) => m.id === llm)

  return (
    <div className="lobby">
      <header>
        <span className="eyebrow">
          <span className="hex" aria-hidden="true">䷼</span> DeepTrust · AgentTrust
        </span>
        <h1>Runtime controls for voice agents</h1>
        <p>
          The agent on the other end is an IT service desk: it resets passwords,
          re-enrolls two-factor, unlocks accounts, enrolls devices and changes mailbox
          rules, and it works from a change ticket that has already been approved.
        </p>
        <ContextNote cfg={cfg} />
      </header>

      <MicCheck
        deviceId={deviceId}
        onDevice={onDevice}
        onConfirmed={setMicOk}
        confirmed={micOk}
      />

      <div className="platpick">
        <span className="eyebrow">2 · Choose the platform</span>
        <div className="platrow">
          <button
            type="button"
            className={platform === 'livekit' ? 'sel' : ''}
            onClick={() => setPlatform('livekit')}
          >
            LiveKit
            <span>our worker, our tools, gate inline in the tool call</span>
          </button>
          <button
            type="button"
            className={platform === 'elevenlabs' ? 'sel' : ''}
            onClick={() => setPlatform('elevenlabs')}
          >
            ElevenLabs
            <span>their agent and their voice, gate inline in the tool call</span>
          </button>
        </div>
        <p className="mchint">
          The same analysis and the same evidence record on either one. What changes
          is where the agent runs and how a finding reaches it.
        </p>
      </div>

      {platform === 'livekit' && (
      <div className="modelpick">
        <div className="mcrow">
          <span className="eyebrow">3 · Choose the model</span>
          {chosen && (
            <span className={chosen.reasoning ? 'mdlwarn' : 'mdlok'}>
              {chosen.reasoning ? 'thinks before answering' : 'no thinking'} · ~{chosen.ms}ms
            </span>
          )}
        </div>
        <select value={llm} onChange={(e) => setLlm(e.target.value)}>
          {models.map((m) => (
            <option key={m.id} value={m.id} disabled={!m.available}>
              {m.label} · {m.ms}ms{m.reasoning ? ' · reasoning' : ''}
              {m.available ? '' : ' · key unavailable'}
            </option>
          ))}
        </select>
        <p className="mchint">
          {chosen?.note} Reasoning models pause to think before they speak, which on a
          phone call is just silence.
        </p>
      </div>
      )}

      <div className={`enfpick ${enforcement ? '' : 'off'}`}>
        <span className="eyebrow">{platform === 'elevenlabs' ? 3 : 4} · Enforcement</span>
        <div className="enfrow">
          <button type="button" className={enforcement ? 'sel' : ''}
                  onClick={() => setEnforcement(true)}>
            Gate on
          </button>
          <button type="button" className={enforcement ? '' : 'sel'}
                  onClick={() => setEnforcement(false)}>
            Gate off
          </button>
        </div>
        <p className="mchint">
          {enforcement
            ? 'Every finding the analysis makes reaches the agent while the call is still happening. You can throw this switch again mid-call.'
            : 'The agent still has every rule in its prompt \u2014 which is how these are built today. The gate watches and records, it just cannot stop anything, and nothing it finds reaches the agent.'}
        </p>
      </div>

      {/* A name and a role, and that is all there is to give. Both are a claim
          the caller makes on the way in: nothing here checks either, so the
          picker offers no way to say otherwise. */}
      <div className="custom">
        <span className="eyebrow">
          {platform === 'elevenlabs' ? 4 : 5} · Say who is calling
        </span>
        <div className="row">
          <input placeholder="Your name" value={name} onChange={(e) => setName(e.target.value)} />
          <select value={role} onChange={(e) => setRole(e.target.value)}>
            {roles.map((r) => (
              <option key={r} value={r}>
                {r}
              </option>
            ))}
          </select>
          <span className="pver no">unverified</span>
          <button disabled={busy || !name || !micOk} onClick={() => join({ name, role })}>
            Call
          </button>
        </div>
        <p className="mchint">
          The call starts with the name and the role you typed, and with nothing
          confirming either. Neither the agent nor the panel will say the caller is
          verified, because nothing here can verify anyone.
        </p>
      </div>
    </div>
  )
}

function Call({ conn, onLeave, cfg }: { conn: LiveKitConn; onLeave: () => void; cfg: Cfg }) {
  const [events, setEvents] = useState<CaEvent[]>([])
  const { state, audioTrack } = useVoiceAssistant()
  const { localParticipant, microphoneTrack, isMicrophoneEnabled } = useLocalParticipant()

  const send = useCallback(
    (text: string) => {
      void localParticipant.sendText(text, { topic: 'lk.chat' })
    },
    [localParticipant],
  )

  // The gate switch. Seeded by the pre-call choice, which travels in the join
  // metadata and so cannot be lost, then thrown live over the data channel.
  //
  // publishData is fire-and-forget: a packet sent before the agent has joined
  // the room goes nowhere. Flipping the switch the instant you connect, which
  // is the most natural thing to do, was silently doing nothing while the UI
  // happily said "gate off". So the agent's echo is the authority, and we keep
  // asking until it answers.
  const [gateOn, setGateOn] = useState(conn.profile.enforcement !== false)
  const [gateWant, setGateWant] = useState<boolean | null>(null)
  const pending = gateWant !== null && gateWant !== gateOn

  useEffect(() => {
    if (!pending) return
    let alive = true
    const push = () =>
      void localParticipant.publishData(
        new TextEncoder().encode(JSON.stringify({ type: 'enforcement', on: gateWant })),
        { topic: 'ctl', reliable: true },
      )
    push()
    const t = setInterval(() => {
      if (alive) push()
    }, 600)
    return () => {
      alive = false
      clearInterval(t)
    }
  }, [pending, gateWant, localParticipant])

  useDataChannel('ca', (msg) => {
    try {
      const ev = JSON.parse(new TextDecoder().decode(msg.payload))
      // stamp on arrival; deriving it at render time resets on every rebuild
      ev._rx = Date.now()
      if (ev.type === 'enforcement') {
        setGateOn(Boolean(ev.on))
        setGateWant(null)
      }
      setEvents((prev) => [...prev, ev])
    } catch {
      /* ignore malformed */
    }
  })

  return (
    <CallShell
      profile={conn.profile}
      platform="LiveKit"
      events={events}
      cfg={cfg}
      gateOn={gateOn}
      gateLabel={
        pending ? (gateWant ? 'Connecting\u2026' : 'Disconnecting\u2026') : gateOn ? 'Gate on' : 'Gate off'
      }
      onGate={(on) => setGateWant(on)}
      onLeave={onLeave}
      onSend={send}
      sessionId={conn.room}
      viz={
        <>
          <BarVisualizer state={state} barCount={7} trackRef={audioTrack} />
          <span className="state">{state}</span>
        </>
      }
      micControls={
        <>
          <span className={`micdot ${isMicrophoneEnabled ? 'on' : 'off'}`} />
          <span className="miclabel">
            {isMicrophoneEnabled
              ? microphoneTrack?.isMuted
                ? 'mic muted'
                : 'mic live'
              : 'mic off'}
          </span>
          <button
            className="ghost"
            onClick={() => void localParticipant.setMicrophoneEnabled(!isMicrophoneEnabled)}
          >
            {isMicrophoneEnabled ? 'Mute' : 'Enable mic'}
          </button>
        </>
      }
    />
  )
}

export default function App() {
  const [conn, setConn] = useState<Conn | null>(null)
  const [cfg, setCfg] = useState<Cfg>(EMPTY_CFG)

  // The organisation's own runbook, procedures and controls, which the example
  // server reads from the DeepTrust API with its organisation key. Whatever
  // comes back is what is shown, including nothing at all.
  useEffect(() => {
    fetch(`${serverFor('elevenlabs')}/sop`)
      .then((r) => r.json())
      .then((d) =>
        setCfg({
          runbook: d.runbook ?? null,
          sops: d.sops ?? [],
          documents: d.documents ?? [],
          controls: d.controls ?? [],
          protectedAccounts: d.protected_accounts ?? [],
          reason: d.reason ?? 'malformed_response',
          source: d.source ?? '',
        }),
      )
      .catch(() => setCfg({ ...EMPTY_CFG, reason: 'backend_unreachable' }))
  }, [])
  const [err, setErr] = useState<string | null>(null)
  // A blocked microphone must not tear the call down: the text composer still
  // drives the agent, which is the whole point of the panel.
  const [mic, setMic] = useState(true)
  const [deviceId, setDeviceId] = useState('')
  const connected = useRef(false)

  if (!conn)
    return (
      <>
        {err && (
          <div className="banner">
            {err}
            <button className="ghost" onClick={() => setErr(null)}>
              dismiss
            </button>
          </div>
        )}
        <Lobby
          cfg={cfg}
          deviceId={deviceId}
          onDevice={setDeviceId}
          onJoin={(c) => {
            setErr(null)
            setMic(true) // a past mic error must not silence every later call
            setConn(c)
          }}
        />
      </>
    )

  if (conn.platform === 'elevenlabs')
    return <ElevenCall conn={conn} cfg={cfg} onLeave={() => setConn(null)} />

  return (
    <LiveKitRoom
      token={conn.token}
      serverUrl={conn.url}
      connect
      audio={mic}
      video={false}
      options={
        deviceId ? { audioCaptureDefaults: { deviceId } } : undefined
      }
      onError={(e) => {
        if (/permission|denied|NotAllowed|NotFound|device/i.test(e.message)) {
          setErr(`Microphone unavailable (${e.name}). Continuing without it, use the text box.`)
          setMic(false)
          return
        }
        setErr(`${e.name}: ${e.message}`)
      }}
      onConnected={() => {
        connected.current = true
      }}
      onDisconnected={() => {
        if (connected.current) {
          connected.current = false
          setConn(null)
        }
      }}
    >
      {/*
        Without this the agent is inaudible: LiveKitRoom subscribes to the
        agent's audio track but nothing attaches it to an audio element, so the
        transcript fills in and the room stays silent. BarVisualizer draws the
        same track without playing it, which makes the failure look like a
        working call.
      */}
      <RoomAudioRenderer />
      {/*
        Chrome will not start audio without a gesture, and the click that began
        the call does not always count once the track arrives later. This
        renders a prompt when playback is blocked and nothing when it is not.
      */}
      <StartAudio label="Click to enable audio" />
      <Call conn={conn} cfg={cfg} onLeave={() => setConn(null)} />
    </LiveKitRoom>
  )
}
