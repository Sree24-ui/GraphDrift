import {
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
} from 'react'

import { getGraphReplay } from '../api/client'
import type { GraphSnapshot } from '../api/types'
import {
  REPLAY_STEPS,
  REPLAY_STEP_MS,
  REPLAY_WINDOW_MS,
  computeReplayRange,
  endMsToStep,
  formatTimelineTimestamp,
  stepToEndMs,
} from '../utils/replay'
import {
  flaggedAccountIds,
  findNewlyFlagged,
} from '../utils/replayFlagged'

export type TimelineMode = 'live' | 'replay'

interface TimelineScrubberProps {
  mode: TimelineMode
  onModeChange: (mode: TimelineMode) => void
  onSnapshotChange: (
    snapshot: GraphSnapshot | null,
    meta: { endMs: number; newlyFlagged: string[] },
  ) => void
  onLoadingChange?: (loading: boolean) => void
  onPlayingChange?: (playing: boolean) => void
}

async function probeEarliestEndMs(
  nowMs: number,
): Promise<{ earliestEndMs: number; dataFloorMs: number }> {
  for (let step = 0; step <= REPLAY_STEPS; step += 1) {
    const endMs = stepToEndMs(step, nowMs)
    const startMs = endMs - REPLAY_WINDOW_MS
    try {
      const snapshot = await getGraphReplay(
        new Date(startMs).toISOString(),
        new Date(endMs).toISOString(),
      )
      if (snapshot.edges.length > 0 || snapshot.nodes.length > 0) {
        const dataFloorMs = new Date(snapshot.window_start).getTime()
        return { earliestEndMs: endMs, dataFloorMs }
      }
    } catch {
      continue
    }
  }
  return {
    earliestEndMs: nowMs,
    dataFloorMs: nowMs - REPLAY_WINDOW_MS,
  }
}

export default function TimelineScrubber({
  mode,
  onModeChange,
  onSnapshotChange,
  onLoadingChange,
  onPlayingChange,
}: TimelineScrubberProps) {
  const [step, setStep] = useState(REPLAY_STEPS)
  const [isPlaying, setIsPlaying] = useState(false)
  const [isLoading, setIsLoading] = useState(false)
  const [earliestEndMs, setEarliestEndMs] = useState<number | null>(null)
  const [dataFloorMs, setDataFloorMs] = useState(0)
  const [displayEndMs, setDisplayEndMs] = useState(() => Date.now())

  const prevFlaggedRef = useRef<Set<string>>(new Set())
  const playTimerRef = useRef<number | null>(null)
  const fetchTokenRef = useRef(0)
  const boundaryProbedRef = useRef(false)

  const nowMs = Date.now()
  const minStep = useMemo(() => {
    if (earliestEndMs === null) return 0
    return endMsToStep(earliestEndMs, nowMs)
  }, [earliestEndMs, nowMs])

  const fetchReplayAtStep = useCallback(
    async (targetStep: number, resetFlaggedMemory = false) => {
      const token = ++fetchTokenRef.current
      const endMs = stepToEndMs(targetStep)
      const { start, end } = computeReplayRange(
        endMs,
        dataFloorMs || endMs - REPLAY_WINDOW_MS,
      )

      setIsLoading(true)
      onLoadingChange?.(true)
      try {
        const snapshot = await getGraphReplay(
          new Date(start).toISOString(),
          new Date(end).toISOString(),
        )
        if (token !== fetchTokenRef.current) return

        if (resetFlaggedMemory) {
          prevFlaggedRef.current = new Set()
        }

        const currentFlagged = flaggedAccountIds(snapshot)
        const newlyFlagged = findNewlyFlagged(
          prevFlaggedRef.current,
          currentFlagged,
        )
        prevFlaggedRef.current = currentFlagged

        setDisplayEndMs(end)
        onSnapshotChange(snapshot, { endMs: end, newlyFlagged })
      } catch {
        if (token === fetchTokenRef.current) {
          onSnapshotChange(null, { endMs, newlyFlagged: [] })
        }
      } finally {
        if (token === fetchTokenRef.current) {
          setIsLoading(false)
          onLoadingChange?.(false)
        }
      }
    },
    [dataFloorMs, onLoadingChange, onSnapshotChange],
  )

  const stopPlayback = useCallback(() => {
    if (playTimerRef.current !== null) {
      window.clearInterval(playTimerRef.current)
      playTimerRef.current = null
    }
    setIsPlaying(false)
    onPlayingChange?.(false)
  }, [onPlayingChange])

  const handleModeChange = useCallback(
    async (nextMode: TimelineMode) => {
      if (nextMode === mode) return

      if (nextMode === 'live') {
        stopPlayback()
        onModeChange('live')
        onSnapshotChange(null, { endMs: Date.now(), newlyFlagged: [] })
        return
      }

      onModeChange('replay')
      stopPlayback()

      if (!boundaryProbedRef.current) {
        boundaryProbedRef.current = true
        const { earliestEndMs: earliest, dataFloorMs: floor } =
          await probeEarliestEndMs(Date.now())
        setEarliestEndMs(earliest)
        setDataFloorMs(floor)
        const earliestStep = endMsToStep(earliest)
        const startStep = Math.max(earliestStep, 0)
        setStep(startStep)
        await fetchReplayAtStep(startStep, true)
        return
      }

      setStep(REPLAY_STEPS)
      await fetchReplayAtStep(REPLAY_STEPS, true)
    },
    [fetchReplayAtStep, mode, onModeChange, onSnapshotChange, stopPlayback],
  )

  const handleSliderChange = useCallback(
    (nextStep: number) => {
      const clamped = Math.max(minStep, Math.min(REPLAY_STEPS, nextStep))
      setStep(clamped)
      void fetchReplayAtStep(clamped)
    },
    [fetchReplayAtStep, minStep],
  )

  const handlePlay = useCallback(() => {
    if (isPlaying) return

    stopPlayback()
    const startStep = minStep
    setStep(startStep)
    prevFlaggedRef.current = new Set()
    setIsPlaying(true)
    onPlayingChange?.(true)

    void fetchReplayAtStep(startStep, true).then(() => {
      let current = startStep
      playTimerRef.current = window.setInterval(() => {
        current += 1
        if (current > REPLAY_STEPS) {
          stopPlayback()
          return
        }
        setStep(current)
        void fetchReplayAtStep(current)
      }, 1000)
    })
  }, [
    fetchReplayAtStep,
    isPlaying,
    minStep,
    onPlayingChange,
    stopPlayback,
  ])

  const handleStop = useCallback(() => {
    stopPlayback()
    setStep(REPLAY_STEPS)
    void fetchReplayAtStep(REPLAY_STEPS, true)
  }, [fetchReplayAtStep, stopPlayback])

  useEffect(() => {
    return () => {
      stopPlayback()
    }
  }, [stopPlayback])

  const sliderProgress =
    REPLAY_STEPS === 0 ? 100 : (step / REPLAY_STEPS) * 100
  const boundaryProgress =
    REPLAY_STEPS === 0 ? 0 : (minStep / REPLAY_STEPS) * 100

  return (
    <div className="glass-panel rounded-xl px-4 py-3">
      <div className="mb-3 flex flex-wrap items-center justify-between gap-3">
        <div className="flex items-center gap-2">
          <span className="text-xs font-medium text-on-surface-variant">
            Graph mode
          </span>
          <div className="flex overflow-hidden rounded-lg border border-primary/20">
            <button
              type="button"
              onClick={() => void handleModeChange('live')}
              className={[
                'px-3 py-1.5 text-xs font-medium transition-colors',
                mode === 'live'
                  ? 'bg-primary/20 text-primary'
                  : 'bg-surface text-on-surface-variant hover:text-on-surface',
              ].join(' ')}
            >
              Live
            </button>
            <button
              type="button"
              onClick={() => void handleModeChange('replay')}
              className={[
                'px-3 py-1.5 text-xs font-medium transition-colors',
                mode === 'replay'
                  ? 'bg-tertiary/20 text-tertiary'
                  : 'bg-surface text-on-surface-variant hover:text-on-surface',
              ].join(' ')}
            >
              Replay
            </button>
          </div>
        </div>

        {mode === 'replay' && (
          <div className="flex items-center gap-2">
            <button
              type="button"
              onClick={handlePlay}
              disabled={isPlaying || isLoading}
              className="rounded-lg border border-primary/30 bg-primary/10 px-3 py-1 text-xs text-primary disabled:opacity-40"
            >
              Play
            </button>
            <button
              type="button"
              onClick={stopPlayback}
              disabled={!isPlaying}
              className="rounded-lg border border-primary/10 px-3 py-1 text-xs text-on-surface-variant disabled:opacity-40"
            >
              Pause
            </button>
            <button
              type="button"
              onClick={handleStop}
              disabled={isLoading}
              className="rounded-lg border border-primary/10 px-3 py-1 text-xs text-on-surface-variant disabled:opacity-40"
            >
              Stop
            </button>
          </div>
        )}
      </div>

      {mode === 'replay' ? (
        <>
          <p className="mb-2 text-center text-xs tabular-nums text-on-surface">
            {formatTimelineTimestamp(displayEndMs)}
            {isLoading && (
              <span className="ml-2 text-on-surface-variant">
                Loading snapshot…
              </span>
            )}
          </p>

          <div className="relative px-1">
            {minStep > 0 && (
              <div
                className="absolute left-1 top-1/2 h-1.5 -translate-y-1/2 rounded-l-full bg-outline-variant/80"
                style={{ width: `${boundaryProgress}%` }}
                title="No transaction data before this point"
              />
            )}

            <input
              type="range"
              min={0}
              max={REPLAY_STEPS}
              step={1}
              value={step}
              disabled={isPlaying || isLoading}
              onChange={(e) => handleSliderChange(Number(e.target.value))}
              className="timeline-slider w-full"
              style={{
                background: `linear-gradient(to right, #7dd3fc ${sliderProgress}%, #1a2438 ${sliderProgress}%)`,
              }}
            />

            <div className="mt-1 flex justify-between text-[10px] text-on-surface-variant">
              <span>
                {earliestEndMs
                  ? formatTimelineTimestamp(earliestEndMs)
                  : '−15 min'}
              </span>
              <span>30s steps · {REPLAY_STEP_MS / 1000}s each</span>
              <span>Now</span>
            </div>
          </div>
        </>
      ) : (
        <p className="text-xs text-on-surface-variant">
          Live mode — graph updates in real time from the WebSocket feed.
          Switch to Replay to scrub through the last 15 minutes.
        </p>
      )}
    </div>
  )
}
