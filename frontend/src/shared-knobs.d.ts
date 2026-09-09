declare module '../../shared/detection_knobs.json' {
  const knobs: {
    window_minutes: number
    secondary_window_minutes: number
    min_transactions_for_scoring: number
    amount_entropy_bins: number
    gdi_min: number
    gdi_max: number
    raw_distance_clip: number
    min_accounts_for_full_cov: number
    variance_floor: number
    shrinkage_alpha: number
    fusion_gdi_weight: number
    fusion_ring_weight: number
    score_escalation_relative_threshold: number
    default_alert_top_percent: number
    manual_alert_top_percent_min: number
    manual_alert_top_percent_max: number
    detection_cycle_interval_seconds: number
    metrics_broadcast_interval_seconds: number
    simulation_interval_seconds: number
    mule_attack_probability: number
    slow_drip_attack_probability: number
    pool_size: number
    alert_staleness_hours: number
    min_ring_member_count: number
    risk_threshold: number
    louvain_resolution: number
    community_similarity_threshold: number
    max_partition_snapshots: number
    ring_hub_weight: number
    ring_external_weight: number
    ring_recent_weight: number
    confidence_strong_percentile: number
    peripheral_hub_connection_base: number
    peripheral_pattern_consistency_bonus: number
    peripheral_min_qualifying_score: number
    min_reviewed_sample: number
    calibration_window: number
    target_band_low: number
    target_band_high: number
    calibration_step_percent_points: number
    calibration_clamp_low_percent: number
    calibration_clamp_high_percent: number
    calibration_consecutive_side_required: number
    cycles_per_calibration: number
    replay_step_seconds: number
  }
  export default knobs
}
