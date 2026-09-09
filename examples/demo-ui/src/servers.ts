/**
 * Where each platform's example server lives.
 *
 * One server per platform, each serving both halves: the platform bits
 * (`/token` or `/eleven/session`) and the DeepTrust analysis (`/utterance`,
 * `/nudges`, `/record`). Ports sit clear of the DeepTrust stack, which uses
 * 3000, 8000, 8080, 4200 and 54322.
 */

export type Platform = 'livekit' | 'elevenlabs'

const DEFAULTS: Record<Platform, string> = {
  livekit: 'http://127.0.0.1:8101',
  elevenlabs: 'http://127.0.0.1:8102',
}

export const serverFor = (platform: Platform): string => {
  const override =
    platform === 'livekit'
      ? import.meta.env.VITE_LIVEKIT_SERVER
      : import.meta.env.VITE_ELEVENLABS_SERVER
  return override ?? DEFAULTS[platform]
}

/**
 * The origin for the call in progress.
 *
 * The panel and the call surfaces render only after a platform is chosen, so
 * they read this rather than taking it through several layers of props. Set
 * once when the call is joined.
 */
let current: string = DEFAULTS.livekit

export const setCurrentServer = (platform: Platform) => {
  current = serverFor(platform)
}

export const currentServer = () => current
