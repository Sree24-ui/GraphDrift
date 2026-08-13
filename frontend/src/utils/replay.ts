export const REPLAY_WINDOW_MS = 15 * 60 * 1000
export const REPLAY_STEP_MS = 30 * 1000
export const REPLAY_STEPS = REPLAY_WINDOW_MS / REPLAY_STEP_MS

export function stepToEndMs(step: number, nowMs: number = Date.now()): number {
  const clamped = Math.max(0, Math.min(REPLAY_STEPS, step))
  return nowMs - (REPLAY_STEPS - clamped) * REPLAY_STEP_MS
}

export function endMsToStep(endMs: number, nowMs: number = Date.now()): number {
  const offset = nowMs - endMs
  const raw = REPLAY_STEPS - Math.round(offset / REPLAY_STEP_MS)
  return Math.max(0, Math.min(REPLAY_STEPS, raw))
}

export function snapEndMs(endMs: number, nowMs: number = Date.now()): number {
  return stepToEndMs(endMsToStep(endMs, nowMs), nowMs)
}

export function formatTimelineTimestamp(ms: number): string {
  return new Date(ms).toLocaleString('en-IN', {
    month: 'short',
    day: 'numeric',
    hour: '2-digit',
    minute: '2-digit',
    second: '2-digit',
    hour12: false,
  })
}

export function computeReplayRange(
  endMs: number,
  dataFloorMs: number,
): { start: number; end: number } {
  const end = endMs
  const idealStart = end - REPLAY_WINDOW_MS
  const start = Math.max(dataFloorMs, idealStart)
  return { start, end }
}
