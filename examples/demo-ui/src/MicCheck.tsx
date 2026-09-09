import { useCallback, useEffect, useRef, useState } from 'react'

type Device = { deviceId: string; label: string }

/**
 * Inputs that present as working microphones and produce silence. Meeting apps
 * and capture tools install these, and macOS will happily make one the system
 * default.
 */
const VIRTUAL = /virtual|teams|zoom|pickle|obs|loopback|blackhole|soundflower|aggregate/i

/** Pick an input and prove it works before joining. A silent virtual device
 *  looks identical to a working one until you watch the level. */
export function MicCheck({
  deviceId,
  onDevice,
  onConfirmed,
  confirmed,
}: {
  deviceId: string
  onDevice: (id: string) => void
  onConfirmed: (ok: boolean) => void
  confirmed: boolean
}) {
  const [devices, setDevices] = useState<Device[]>([])
  const [level, setLevel] = useState(0)
  const [peak, setPeak] = useState(0)
  const [err, setErr] = useState<string | null>(null)

  const streamRef = useRef<MediaStream | null>(null)
  const ctxRef = useRef<AudioContext | null>(null)
  const confirmedRef = useRef(false)
  const rafRef = useRef<number>(0)

  const stop = useCallback(() => {
    cancelAnimationFrame(rafRef.current)
    streamRef.current?.getTracks().forEach((t) => t.stop())
    streamRef.current = null
    void ctxRef.current?.close()
    ctxRef.current = null
  }, [])

  // Enumerate once. Labels are only populated after permission is granted.
  useEffect(() => {
    navigator.mediaDevices
      .enumerateDevices()
      .then((all) => {
        const inputs = all
          .filter((d) => d.kind === 'audioinput')
          .map((d) => ({ deviceId: d.deviceId, label: d.label || 'Microphone' }))
        setDevices(inputs)

        // The system default is whatever the OS last handed to a meeting app,
        // which on a laptop that runs any of them is a virtual device that
        // opens cleanly and returns silence. Start on something real instead,
        // so the first run is not a flat meter and a mystery.
        if (!deviceId) {
          const real = inputs.find(
            (d) => !VIRTUAL.test(d.label) && d.deviceId !== 'default',
          )
          if (real) onDevice(real.deviceId)
        }
      })
      .catch((e) => setErr(String(e)))
    // Runs once: this only picks an opening device, and the picker owns it after.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  // Open the selected device and meter it.
  useEffect(() => {
    let cancelled = false
    stop()
    setLevel(0)
    setPeak(0)
    onConfirmed(false)

    navigator.mediaDevices
      .getUserMedia({
        audio: deviceId ? { deviceId: { exact: deviceId } } : true,
      })
      .then((stream) => {
        if (cancelled) {
          stream.getTracks().forEach((t) => t.stop())
          return
        }
        setErr(null)
        streamRef.current = stream
        const ctx = new AudioContext()
        ctxRef.current = ctx
        const analyser = ctx.createAnalyser()
        analyser.fftSize = 1024
        ctx.createMediaStreamSource(stream).connect(analyser)
        const buf = new Uint8Array(analyser.fftSize)

        const tick = () => {
          analyser.getByteTimeDomainData(buf)
          let max = 0
          for (const v of buf) max = Math.max(max, Math.abs(v - 128))
          const norm = Math.min(1, max / 40) // 40/128 is a normal speaking peak
          setLevel(norm)
          setPeak((p) => Math.max(p, norm))
          // Outside the updater: React may run an updater during render, and
          // telling the parent from there is a setState on another component
          // mid-render, which React discards along with whatever else that
          // render was carrying.
          if (norm > 0.25 && !confirmedRef.current) {
            confirmedRef.current = true
            onConfirmed(true)
          }
          rafRef.current = requestAnimationFrame(tick)
        }
        tick()
      })
      .catch((e) => setErr(`${e.name}: ${e.message}`))

    return () => {
      cancelled = true
      stop()
    }
    // onConfirmed is stable enough for this component's lifetime
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [deviceId, stop])

  return (
    <div className="miccheck">
      <div className="mcrow">
        <span className="eyebrow">1 · Check your microphone</span>
        {confirmed && <span className="mcok">✓ hearing you</span>}
      </div>

      <div className="mcrow">
        <select value={deviceId} onChange={(e) => onDevice(e.target.value)}>
          <option value="">System default</option>
          {devices.map((d) => (
            <option key={d.deviceId} value={d.deviceId}>
              {d.label}
            </option>
          ))}
        </select>
      </div>

      <div className="meter" role="meter" aria-label="microphone level">
        <div className="meterfill" style={{ width: `${level * 100}%` }} />
        <div className="meterpeak" style={{ left: `${peak * 100}%` }} />
      </div>

      {!confirmed && (
        <button className="mcskip" onClick={() => onConfirmed(true)}>
          Continue without a microphone, type instead
        </button>
      )}

      <p className="mchint">
        {err
          ? `Could not open that device. ${err}`
          : confirmed
            ? 'Good. Say who is calling below.'
            : 'Say something. If the bar stays flat, choose a different device. Virtual mics (Teams, Zoom, Pickle) look connected but produce silence.'}
      </p>
    </div>
  )
}
