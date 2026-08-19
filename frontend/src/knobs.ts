import knobs from '../../shared/detection_knobs.json'

export const WINDOW_MINUTES = knobs.window_minutes
export const SECONDARY_WINDOW_MINUTES = knobs.secondary_window_minutes
export const GDI_MAX = knobs.gdi_max
export const DEFAULT_ALERT_TOP_PERCENT = knobs.default_alert_top_percent
export const DEFAULT_ALERT_TOP_PERCENTILE =
  1 - knobs.default_alert_top_percent / 100
export const MANUAL_ALERT_TOP_PERCENT_MIN = knobs.manual_alert_top_percent_min
export const MANUAL_ALERT_TOP_PERCENT_MAX = knobs.manual_alert_top_percent_max
export const MIN_REVIEWED_SAMPLE = knobs.min_reviewed_sample
export const REPLAY_WINDOW_MS = knobs.window_minutes * 60 * 1000
export const REPLAY_STEP_MS = knobs.replay_step_seconds * 1000
export const REPLAY_STEPS = REPLAY_WINDOW_MS / REPLAY_STEP_MS
