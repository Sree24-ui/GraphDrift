"""
IBM HI-Small AML evaluation timing (isolated from production WINDOW_MINUTES).

HI-Small timestamps are wall-clock datetimes at minute resolution.
Labeled typology instances in HI-Small_Patterns.txt last a median of ~75 hours
(first→last transaction, pre-compression). PaySim's ÷20 is the wrong analog:
after ÷20 the median ring is still ~3.7 hours, far outside the 15-min / 60-min
multi-scale windows.

Compression is set so the median real ring occupies the same relative slot in
the 60-min slow window as our simulator slow-drip attacks (12–15 min, ~22.5%
of 60 min → target 13.5 compressed minutes):

  scale = median_ring_minutes / 13.5 ≈ 332

After compression: median ≈ 13.5 min, p90 ≈ 20 min, max ≈ 37 min — all inside
the 60-min slow window; typical rings sit near the 15-min fast window the way
slow-drip sits near that window's upper end.

If the source span is already short (<12 IBM hours), compression is skipped
(scale = 1) so we do not over-squeeze a compact trace.
"""

from app.constants import SECONDARY_WINDOW_MINUTES, WINDOW_MINUTES

# Divide IBM elapsed time by this factor when mapping to eval timestamps.
# Derived from HI-Small_Patterns.txt (370 labeled instances, median 4484 min).
IBM_AML_TIME_SCALE = 332

# Wall-clock minutes represented by one IBM hour after compression.
IBM_AML_COMPRESSED_MINUTES_PER_HOUR = 60 / IBM_AML_TIME_SCALE

# Eval uses the same windows as production after compression.
IBM_AML_WINDOW_MINUTES = WINDOW_MINUTES
IBM_AML_SLOW_WINDOW_MINUTES = SECONDARY_WINDOW_MINUTES

# IBM hours covered by one detection window after compression.
IBM_AML_WINDOW_SOURCE_HOURS = IBM_AML_WINDOW_MINUTES / IBM_AML_COMPRESSED_MINUTES_PER_HOUR

# Contiguous IBM-time window used when stratifying (before compression).
IBM_AML_STRATIFY_WINDOW_HOURS = 48
