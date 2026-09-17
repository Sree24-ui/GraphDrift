import knobs from '../../shared/detection_knobs.json'

export const GDI_MAX = knobs.gdi_max
export const DETECTION_CYCLE_INTERVAL_SECONDS =
  knobs.detection_cycle_interval_seconds
export const DEFAULT_ALERT_TOP_PERCENT = knobs.default_alert_top_percent
export const DEFAULT_ALERT_TOP_PERCENTILE =
  1 - knobs.default_alert_top_percent / 100
export const MANUAL_ALERT_TOP_PERCENT_MIN = knobs.manual_alert_top_percent_min
export const MANUAL_ALERT_TOP_PERCENT_MAX = knobs.manual_alert_top_percent_max
export const CALIBRATION_STEP_PERCENT_POINTS =
  knobs.calibration_step_percent_points
export const REPLAY_WINDOW_MS = knobs.window_minutes * 60 * 1000
export const REPLAY_STEP_MS = knobs.replay_step_seconds * 1000
export const REPLAY_STEPS = REPLAY_WINDOW_MS / REPLAY_STEP_MS
