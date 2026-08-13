"""
PaySim-specific evaluation timing (isolated from production WINDOW_MINUTES).

PaySim documentation: 1 step = 1 simulated hour.
For evaluation we proportionally compress step-hours by PAYSIM_TIME_SCALE
(1 step-hour → 3 wall-clock minutes) so relative ordering and spacing are
preserved while the production 15-minute detection window remains meaningful.
"""

# Minutes represented by one PaySim step in the raw dataset.
PAYSIM_MINUTES_PER_STEP = 60

# Divide step-hours by this factor when mapping to wall-clock timestamps.
PAYSIM_TIME_SCALE = 20

# Wall-clock minutes per PaySim step after compression (60 / 20 = 3).
PAYSIM_COMPRESSED_MINUTES_PER_STEP = PAYSIM_MINUTES_PER_STEP / PAYSIM_TIME_SCALE

# Eval uses the same 15-minute window as production after compression.
PAYSIM_WINDOW_MINUTES = 15

# PaySim accounts rarely have ≥3 tx in any window; eval-only scoring gate.
PAYSIM_MIN_TRANSACTIONS_FOR_SCORING = 1

# Equivalent PaySim steps covered by the detection window.
PAYSIM_WINDOW_STEPS = PAYSIM_WINDOW_MINUTES / PAYSIM_COMPRESSED_MINUTES_PER_STEP
