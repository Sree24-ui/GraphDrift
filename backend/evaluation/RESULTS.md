# GraphDrift Offline Evaluation Results

Generated: 2026-08-12T22:33:04

## Datasets

- **Internal snapshot:** `graphdrift_snapshot_2026-08-12.db`
- **PaySim sample:** `paysim_eval.db` (isolated from live demo DB)
- **Snapshot window:** 15 minutes at `max(timestamp)`
- **PaySim window:** 15 minutes at `max(timestamp)` (after ÷20 step compression: 1 step-hour → 3 wall-clock min; ≈5.0 PaySim steps per window)
- **Alert percentile:** top 5% (Layer-1 + fusion)

## Results (rates + raw counts)

| Dataset | Detector | P | R | F1 | FPR | TP | FP | FN | TN | Fraud† | Eval‡ |
|---------|----------|---|---|----|-----|----|----|----|----|--------|-------|
| snapshot | baseline | 1.000 | 0.136 | 0.240 | 0.0000 | 3 | 0 | 19 | 80 | 22 | 102 |
| snapshot | layer1 | 1.000 | 0.273 | 0.429 | 0.0000 | 6 | 0 | 16 | 80 | 22 | 102 |
| snapshot | fusion | 1.000 | 0.455 | 0.625 | 0.0000 | 10 | 0 | 12 | 80 | 22 | 102 |
| snapshot | hybrid | 0.914 | 0.813 | 0.860 | 0.0295 | 74 | 7 | 17 | 230 | 91 | 328 |
| paysim | baseline | 0.000 | 0.000 | 0.000 | 0.0000 | 0 | 0 | 146 | 1843 | 146 | 1989 |
| paysim | layer1 | 0.050 | 0.034 | 0.041 | 0.0515 | 5 | 95 | 141 | 1748 | 146 | 1989 |
| paysim | fusion | 0.050 | 0.034 | 0.041 | 0.0515 | 5 | 95 | 141 | 1748 | 146 | 1989 |

† **Fraud** = ground-truth fraud accounts in the evaluated universe for that detector row (≥3 tx for baseline/layer1/fusion; all active accounts for hybrid).
‡ **Eval** = total accounts evaluated (scored universe for fusion family; all active accounts for hybrid).

### PaySim time-handling correction

Previous run incorrectly compressed all PaySim rows into a single 15-minute window, distorting velocity/burstiness. This run maps `step` → timestamps with proportional compression (1 step-hour ÷ 20 = 3 min) and uses a 15m detection window.

| Detector | Old F1 (compressed) | New F1 (step-based) |
|----------|--------------------|-----------------------|
| baseline | 0.000 | 0.000 |
| layer1 | 0.000 | 0.041 |
| fusion | 0.200 | 0.041 |

## Hybrid structural pass (snapshot)

After the main fusion pass (min_tx=3), a **peripheral structural** detector flags 1–2 tx accounts connected to top-anomaly hubs with fan-in sender or fan-out receiver leg patterns. Alerts are tagged `detection_method: peripheral_structural` (thinner evidence than Mahalanobis+ring).

| Metric | Fusion only | Hybrid (fusion + peripheral) |
|--------|-------------|------------------------------|
| Precision | 1.000 | 0.914 |
| Recall | 0.455 (10/22 scored) | **0.813 (74/91 all active)** |
| F1 | 0.625 | **0.860** |
| FPR | 0.0000 | 0.0295 (7 FP / 237 benign active) |
| TP / FP / FN | 10 / 0 / 12 | 74 / 7 / 17 |

**Peripheral-only breakdown** (71 accounts not already flagged by fusion):

- **64 TP / 7 FP** → peripheral-only precision **90.1%**
- **All 59 single-transaction fraud accounts caught** (100% of 1-tx fraud in window)
- **5 of 10** two-transaction fraud accounts caught peripherally (remaining 5 missed)
- **17 FN** overall: mostly ≥3-tx fraud below fusion top-k cutoff, plus unmatched 2-tx legs

**False positives** (7 benign accounts with one payment to/from a correctly flagged hub): `dsahni@ybl`, `isaiahgala@ybl`, `lajita75@okhdfc`, `lakshmi26@paytm`, `loganshankar@okicici`, `riya88@okhdfc`, `varughesegeetika@ybl`. These are structurally indistinguishable from real fan-in senders without additional behavioral features.

**Caveat:** Peripheral recall is **cascade-dependent** — it only fires when the hub is already in fusion's top-anomaly set. Wrong hub selection propagates; right hub selection unlocks the long tail of single-leg participants that GDI cannot score.

### Selectivity audit (true selection universe)

The headline hybrid FPR (7/237 benign active = 2.95%) is misleadingly low because **most active accounts are not 1-hop neighbors of a flagged hub** and were never candidates. The real population `score_peripheral_accounts` chooses from:

| Population | Count |
|------------|-------|
| 1–2 tx accounts, 1-hop from a fusion hub | **76** |
| — fraud-involved | 69 |
| — benign | 7 |

**Metrics within this universe** (not vs all 328 active accounts):

| Detector variant | Flagged | TP | FP | FN | TN | P | R | FPR† |
|------------------|---------|----|----|----|-----|---|---|------|
| Peripheral pass (actual) | 71 | 64 | 7 | 5 | **0** | 0.901 | 0.928 | **1.000** |
| Hub-only (flag all hub neighbors) | 76 | 69 | 7 | 0 | **0** | 0.908 | 1.000 | **1.000** |
| Directional filter only | 71 | 64 | 7 | 5 | **0** | 0.901 | 0.928 | **1.000** |

† FPR within universe = FP / benign hub-neighbors. **All 7 benign hub-neighbors are flagged; zero are correctly left unflagged.**

Flag rate within universe: **fraud 92.8%**, **benign 100.0%**. The low global FP count reflects **scarcity of benign hub-neighbors in this snapshot** (7 total), not selective discrimination between fraud and benign legs.

**Directional filter (`in_deg=0` sender / `out_deg=0` receiver):** excludes only **5** hub-neighbors — all **fraud** (2-tx accounts with `in_deg=1, out_deg=1`, i.e. both paid and received). It does **not** exclude any benign account. Precision is identical whether or not the directional check is applied (7 FPs in all variants); hub-only actually has *higher* recall (69/69 fraud neighbors vs 64/69).

**Honest characterization for the paper:** this is best described as **"guilt by 1-hop association with a fusion-flagged hub, conditioned on a fan-in-sender or fan-out-receiver leg shape"** — not independent structural pattern discovery. The leg-shape filter is a **coverage gate** (excludes 5 bidirectional 2-tx fraud legs) rather than a precision discriminator. On an adversarial decoy (5 injected benign 1-tx fan-in senders directly to a flagged hub), **5/5 are flagged** — structure cannot separate benign one-shot payers from fraud legs when topology is identical. Proximity-only decoys (benign merchant cluster 2 hops from hub, no direct hub tx) correctly produce **0** flags.

Reproduce: `python -m evaluation.peripheral_selectivity`

## Interpretation

- **Snapshot:** fusion F1=0.625 (TP=10, FN=12, fraud=22, eval=102) beats baseline F1=0.240.
- **PaySim (corrected timing + rank-based threshold):** fusion F1=0.041 (TP=5, FP=95, FN=141, fraud=146, eval=1989) vs baseline F1=0.000. Prior bug: `score >= np.percentile` with tied GDI scores flagged 996/1989 (~50%); rank-based top-k=100 restores the intended 5% alert budget.

## Snapshot recall by attack type (fusion)

### fusion — recall by attack type

| Attack type | In window | Scored (≥3 tx) | Filtered out | Detected | Recall (scored) |
|-------------|-----------|----------------|--------------|----------|-----------------|
| fast_fan_in_fan_out | 90 | 21 | 69 | 9 | 42.9% |
| slow_drip | 1 | 1 | 0 | 1 | 100.0% |
| all_synthetic | 91 | 22 | 69 | 10 | 45.5% |

### Why snapshot recall is below 50%

This is **not** primarily a detector-quality issue — it is a **coverage** issue:

- **91** fraud-involved accounts appear in the 15-minute window.
- **69 (76%)** never enter the scored universe because they have fewer than 3 transactions in the window (typical fan-in *senders* and fan-out *receivers* each participate in only 1–2 legs).
- Only **22** fraud accounts are scored; fusion detects **10** → **45.5% recall** on the scored subset, **11.0%** of all fraud-involved accounts in the window.

Among **scored** fast fan-in/fan-out accounts (n=21), fusion recall is **42.9%** (9/21). Misses are not random — they fall just below the top-5% fused-score cutoff:

- **Fan-in senders** (`in_deg=0`, `out_deg≥3`): low GDI, occasionally boosted by ring percentile but not enough to clear threshold (e.g. `aarnav01@ybl` fused=3.526 vs threshold 3.683).
- **Peripheral mule accounts** with moderate fan activity but unremarkable percentile ranks vs 102 scored peers.

The top-5% alert budget (≈5 slots) structurally limits recall when 22 fraud accounts compete in the same percentile pool.

### Missed attack account diagnosis (fusion)

#### 1. `aarav77@okicici` (fast_fan_in_fan_out)
- **Reason:** Scored but outside top-10 alert budget (fused=2.106, cutoff=3.723, rank 137/185)
- Transactions in window: 5
- Feature vector: in_deg=3, out_deg=2, in_cnt=3, out_cnt=2, velocity=0.33, fan_ratio=1.00, burstiness=0.46
- Scores: GDI=0.907, ring=0.000, fused=2.106 (threshold 3.723, rank 137/102)

#### 2. `aarnav01@ybl` (fast_fan_in_fan_out)
- **Reason:** Scored but outside top-10 alert budget (fused=3.526, cutoff=3.723, rank 11/185)
- Transactions in window: 3
- Feature vector: in_deg=0, out_deg=3, in_cnt=0, out_cnt=3, velocity=0.20, fan_ratio=0.00, burstiness=0.58
- Scores: GDI=1.052, ring=2.220, fused=3.526 (threshold 3.723, rank 11/102)

#### 3. `aarnavramesh@okicici` (fast_fan_in_fan_out)
- **Reason:** Scored but outside top-10 alert budget (fused=2.840, cutoff=3.723, rank 46/185)
- Transactions in window: 4
- Feature vector: in_deg=0, out_deg=4, in_cnt=0, out_cnt=4, velocity=0.27, fan_ratio=0.00, burstiness=0.82
- Scores: GDI=1.362, ring=0.000, fused=2.840 (threshold 3.723, rank 46/102)


### Layer 2 ring detection — PaySim

- Transaction graph: **1989** nodes, **995** edges
- Louvain communities detected: **994**
- Communities with member_count ≥ 4: **0**
- Communities clearing risk threshold (2.0): **0**
- Ring alerts emitted: **0**
- Accounts with non-zero ring risk: **0**

PaySim fraud is predominantly single-hop TRANSFER/CASH_OUT between two accounts. Louvain finds many small components but none form the dense, hub-and-spoke rings Layer 2 is tuned for — **Layer 2 does not fire** on this sample (zero accounts with non-zero ring risk). Fusion on PaySim degenerates to Layer 1 percentile ranking.

### Layer 2 on snapshot (contrast)

- Ring alerts: **7** | accounts with ring risk: **102**

### Alert threshold fix (tie-breaking)

PaySim with `min_transactions=1` collapses most accounts to **2 unique GDI scores** (~99% tied at 0.892). Using `score >= np.percentile(scores, 95)` flags everyone at the tied floor (~50% of accounts). Eval and production now use **rank-based top-k** selection (`top_anomaly_budget`) instead.

## Limitations

- Single `as_of` per dataset; no rolling multi-window average.
- Layer-1 and fusion share the same top-percentile alert budget.
- PaySim: eval-only `min_transactions=1` (PaySim accounts rarely reach ≥3 tx/window); 12k cap.
- Snapshot: ~49 min of live sim data; denominators are modest — interpret rates alongside counts.

## Reproduce

```bash
cd graphdrift/backend
python -m evaluation.load_paysim
python -m evaluation.freeze_snapshot
python -m evaluation.run_eval --skip-paysim-load --skip-snapshot
```
