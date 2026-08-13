"""
Simulation tuning constants.

Attack injection rates are chosen so the demo feels like a real UPI fraud desk:
mule rings are rare relative to legitimate traffic, not a firehose.

Previous defaults (8% mule + 4% slow-drip at 2s intervals) produced ~3.6 attack
bursts per minute — far above what any bank would see. At 2s per loop that was
roughly one synthetic fan-in/fan-out ring every 17 seconds.

Target: ~1–2% combined attack probability → ~0.6 attack events/min at 2s loops,
or about one ring every 1.5–2 minutes. Over a 15–20 minute demo that still
yields ~10–12 catchable synthetic rings while keeping alert volume in the tens
once stale alerts are auto-closed.
"""

# Seconds between simulation loop iterations (one normal tx or one attack burst).
SIMULATION_INTERVAL_SECONDS = 2.0

# Per-loop probability of injecting a burst mule (2–3 min fan-in/fan-out) attack.
MULE_ATTACK_PROBABILITY = 0.015  # 1.5%

# Per-loop probability of injecting a slow-drip (12–15 min) ring attack.
SLOW_DRIP_ATTACK_PROBABILITY = 0.005  # 0.5%

# Combined attack injection ≈ 2.0%; remaining mass is legitimate traffic.
