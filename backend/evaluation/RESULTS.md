# GraphDrift Offline Evaluation Results

Generated: 2026-08-12T22:33:04

## Datasets

- **Internal snapshot (historical single run):** `graphdrift_snapshot_2026-08-12.db` — fusion F1=0.625 is **one seed**, not the system estimate. See **Multi-seed synthetic snapshot robustness**.
- **Multi-seed traces:** `snapshots/multiseed_seed{42,123,7,2026,99}.db` (5,551 steps ≈ 185 min, matched to that snapshot's duration)
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

**The `snapshot` rows above are a single historical run.** Treat fusion F1=0.625 as an anecdote. The estimate to cite is the 5-seed mean in [Multi-seed synthetic snapshot robustness](#multi-seed-synthetic-snapshot-robustness).

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

## Multi-seed synthetic snapshot robustness

The snapshot rows in the table above are **one historical run** (`graphdrift_snapshot_2026-08-12.db`, fusion F1=0.625). This section replaces that single-run framing with **mean ± std (min, max) across 5 seeded offline traces**.

Each seed runs the same simulator for 5,551 steps (185.0 min at 2s/loop), matching that snapshot's duration, then **catch-up normal traffic** until the virtual clock reaches `max(attack timestamp)` so a trailing slow-drip is not scored in an empty window. Detection uses a 15-minute window at `max(timestamp)`. **Universe:** ≥3 transactions for baseline / Layer 1 / fusion / fusion_multiscale (same as the original fusion F1=0.625 row). Hybrid = multi-scale fusion + peripheral cascade on **all active** accounts (same universe as the original hybrid row).

### Generation summary (before detection)

| Seed | Transactions | Accounts | Span (min) | Fast attack events | Slow-drip events | Window fast accts | Window slow accts |
|------|--------------|----------|------------|--------------------|------------------|-------------------|-------------------|
| 42 | 7449 | 998 | 194.6 | 73 | 22 | 84 | 1 |
| 123 | 7859 | 1104 | 201.7 | 82 | 25 | 62 | 2 |
| 7 | 8033 | 1214 | 201.1 | 88 | 30 | 43 | 3 |
| 2026 | 7880 | 1252 | 193.1 | 97 | 26 | 108 | 2 |
| 99 | 7873 | 1169 | 197.8 | 84 | 30 | 118 | 2 |
| *reference 2026-08-12* | 7074 | 1060 | 185.0 | — | — | 90 | 1 |

Seeds: 42, 123, 7, 2026, 99. Reproduce: `python -m evaluation.generate_multi_seed_snapshots` then `python -m evaluation.eval_multi_seed`.

### Per-seed metrics

| Seed | Detector | P | R | F1 | FPR | TP | FP | FN | TN | Fraud | Eval |
|------|----------|---|---|----|-----|----|----|----|----|-------|------|
| 42 | baseline | 0.750 | 0.083 | 0.150 | 0.0095 | 3 | 1 | 33 | 104 | 36 | 141 |
| 42 | layer1 | 0.750 | 0.167 | 0.273 | 0.0190 | 6 | 2 | 30 | 103 | 36 | 141 |
| 42 | fusion | 0.600 | 0.167 | 0.261 | 0.0381 | 6 | 4 | 30 | 101 | 36 | 141 |
| 42 | fusion_multiscale (union) | 0.407 | 0.306 | 0.349 | 0.1524 | 11 | 16 | 25 | 89 | 36 | 141 |
| 42 | hybrid (union+peri) | 0.725 | 0.682 | 0.703 | 0.1930 | 58 | 22 | 27 | 92 | 85 | 199 |
| 123 | baseline | 0.000 | 0.000 | 0.000 | 0.0000 | 0 | 0 | 22 | 118 | 22 | 140 |
| 123 | layer1 | 0.500 | 0.182 | 0.267 | 0.0339 | 4 | 4 | 18 | 114 | 22 | 140 |
| 123 | fusion | 0.600 | 0.273 | 0.375 | 0.0339 | 6 | 4 | 16 | 114 | 22 | 140 |
| 123 | fusion_multiscale (union) | 0.367 | 0.500 | 0.423 | 0.1610 | 11 | 19 | 11 | 99 | 22 | 140 |
| 123 | hybrid (union+peri) | 0.707 | 0.828 | 0.763 | 0.1732 | 53 | 22 | 11 | 105 | 64 | 191 |
| 7 | baseline | 0.500 | 0.045 | 0.083 | 0.0081 | 1 | 1 | 21 | 122 | 22 | 145 |
| 7 | layer1 | 0.500 | 0.182 | 0.267 | 0.0325 | 4 | 4 | 18 | 119 | 22 | 145 |
| 7 | fusion | 0.444 | 0.182 | 0.258 | 0.0407 | 4 | 5 | 18 | 118 | 22 | 145 |
| 7 | fusion_multiscale (union) | 0.194 | 0.273 | 0.226 | 0.2033 | 6 | 25 | 16 | 98 | 22 | 145 |
| 7 | hybrid (union+peri) | 0.536 | 0.652 | 0.588 | 0.2031 | 30 | 26 | 16 | 102 | 46 | 174 |
| 2026 | baseline | 1.000 | 0.100 | 0.182 | 0.0000 | 5 | 0 | 45 | 95 | 50 | 145 |
| 2026 | layer1 | 0.875 | 0.140 | 0.241 | 0.0105 | 7 | 1 | 43 | 94 | 50 | 145 |
| 2026 | fusion | 0.727 | 0.160 | 0.262 | 0.0316 | 8 | 3 | 42 | 92 | 50 | 145 |
| 2026 | fusion_multiscale (union) | 0.419 | 0.260 | 0.321 | 0.1895 | 13 | 18 | 37 | 77 | 50 | 145 |
| 2026 | hybrid (union+peri) | 0.800 | 0.655 | 0.720 | 0.1837 | 72 | 18 | 38 | 80 | 110 | 208 |
| 99 | baseline | 1.000 | 0.060 | 0.113 | 0.0000 | 3 | 0 | 47 | 93 | 50 | 143 |
| 99 | layer1 | 0.750 | 0.120 | 0.207 | 0.0215 | 6 | 2 | 44 | 91 | 50 | 143 |
| 99 | fusion | 0.727 | 0.160 | 0.262 | 0.0323 | 8 | 3 | 42 | 90 | 50 | 143 |
| 99 | fusion_multiscale (union) | 0.455 | 0.300 | 0.361 | 0.1935 | 15 | 18 | 35 | 75 | 50 | 143 |
| 99 | hybrid (union+peri) | 0.783 | 0.600 | 0.679 | 0.2020 | 72 | 20 | 48 | 79 | 120 | 219 |

### Aggregated (mean ± std [min, max], n=5)

| Detector | Precision | Recall | F1 | FPR |
|----------|-----------|--------|----|-----|
| baseline | 0.650 ± 0.418 [0.000, 1.000] | 0.058 ± 0.039 [0.000, 0.100] | 0.106 ± 0.070 [0.000, 0.182] | 0.004 ± 0.005 [0.000, 0.010] |
| layer1 | 0.675 ± 0.168 [0.500, 0.875] | 0.158 ± 0.027 [0.120, 0.182] | 0.251 ± 0.027 [0.207, 0.273] | 0.023 ± 0.010 [0.011, 0.034] |
| fusion | 0.620 ± 0.117 [0.444, 0.727] | 0.188 ± 0.048 [0.160, 0.273] | 0.284 ± 0.051 [0.258, 0.375] | 0.035 ± 0.004 [0.032, 0.041] |
| fusion_multiscale (union) | 0.368 ± 0.103 [0.194, 0.455] | 0.328 ± 0.098 [0.260, 0.500] | **0.336 ± 0.072** [0.226, 0.423] | 0.180 ± 0.022 [0.152, 0.203] |
| hybrid (union+peri) | **0.710 ± 0.105** [0.536, 0.800] | **0.683 ± 0.086** [0.600, 0.828] | **0.691 ± 0.065** [0.588, 0.763] | **0.191 ± 0.013** [0.173, 0.203] |

> These are the **default configuration** (`enable_cohub_scoring: false`) and
> are this project's primary results. Enabling optional co-hub scoring lowers
> all three F1 figures; see [Co-hub scoring (optional, off by default)](#co-hub-scoring-optional-off-by-default).

`fusion_multiscale` and `hybrid` are the **shipped union merge** (independent top-5% at 15m and at 60m, then union; peripheral hubs = that union set). Do **not** cite the retired max-then-global-cut numbers: fusion_multiscale F1 **0.304 ± 0.058** and hybrid F1 **0.644 ± 0.091**. Code: `select_top_anomaly_accounts_multiscale`; `compute_fused_scores_multiscale_max_merge` raises.

On the **same active-account universe**, union fusion alone is F1 0.192 / recall 0.135; peripheral raises recall **+0.548** (to 0.683) and F1 **+0.499** (to 0.691) with FPR +0.014. Do **not** subtract 0.691 − 0.336 as “peripheral lift”: 0.336 is ≥3-tx only.

**Headline fusion (15-min, ≥3-tx universe) vs original 0.625:** the original single-snapshot fusion F1 is **optimistic relative to this distribution (above all 5 seeds; max=0.375)**. Fusion F1 std=0.051 (≤ 0.1); seed-to-seed spread is modest. The paper should report **fusion F1 = 0.284 ± 0.051** (range 0.258–0.375) rather than 0.625 as a point estimate.

Original 0.625 had a small scored-fraud set (22/102) and zero FPs. These traces have ~22–50 scored fraud accounts competing for the same top-5% budget (~7 slots of ~140), so recall is structurally lower. Cite **hybrid F1 = 0.691 ± 0.065** (union+peripheral, corrected merge), not 0.644 or the single-run 0.860.

### Methodology check: live freeze vs offline generator (seed 42 A/B)

The ≥3-tx universe grew from **102** (2026-08-12 freeze) to **~140** (seeded traces) at similar total volume. This is **not** catch-up-normals confounding, and **not** a different attack injector.

**Code paths.** Live `run_simulation` and `generate_offline_trace` share the same `generate_normal_transaction` / `generate_mule_attack` / `generate_slow_drip_attack` roll (2% combined attack). Differences: (1) live uses `datetime.now()` + `asyncio.sleep(2)`; offline uses a virtual clock `+= 2s` and does not sleep; (2) offline optionally continues after `n_steps`. Catch-up **normals** injects *only* legitimate pool txs until `clock >= max(timestamp)`. Live instead keeps the mixed roll going. Catch-up does **not** change the 5551-step body.

**Seed 42 A/B** (same seed, same 5551 steps):

| Variant | as_of | Window legit txs | Scored ≥3 | Fusion F1 |
|---------|-------|------------------|-----------|-----------|
| Original 2026-08-12 freeze | max(ts) | 281 | 102 | 0.625 |
| A catch-up normals (current 5-seed) | max(ts) | 448 | 141 | 0.261 |
| B no extra txs | last loop clock | 442 | 145 | 0.267 |
| B' no extra txs | max(ts) (bug) | 161 | 67 | 0.438 |
| C organic continuation (live roll) | max(ts) | 446 | 143 | 0.222 |

A vs B: scored 141 vs 145, F1 0.261 vs 0.267 — **catch-up does not materially change the result.** B' (eval at max(ts) with no continuation) is the empty-trailing-window bug and **inflates** F1 toward the original 0.625.

**Why 102 vs ~140.** A full 15 minutes of 2s loops is ~441 legitimate txs. The original freeze window had **281** legit txs — only ~10 minutes of loop traffic plus attack timestamps that extend past the last `now()`. Offline A/B evaluate a **full** 15-minute tick window, so more pool accounts reach ≥3 txs (~140 vs 102) and more fraud accounts compete for the same top-5% budget. The 0.625 run was a sparser trailing window, not a different simulator.

**6th trace** (seed 314, organic continuation, then `freeze_snapshot`): scored=140, fusion F1=**0.349**, which sits **inside** the 5-seed range [0.258, 0.375]. The live-style loop does not reproduce 0.625.

**Cite 0.284 ± 0.051.** It is the 15-minute full-window estimate. Do not restore 0.625; that number is the sparse-trailing-window special case.

<!-- /multi-seed -->

## Isolation Forest baselines (multi-seed)

Same 5 snapshots, 15-minute window at `max(timestamp)`, ≥3-tx scored universe, **rank-based top-5%** (`select_top_anomaly_accounts`) — not sklearn `contamination`. Each IsolationForest is fit **fresh on that window** with `random_state=seed`. L1 variant uses the 8 Layer-1 scoring features. All-features adds `hub_concentration` and `external_edge_ratio` from the same Louvain rings fusion uses (`get_ring_alerts` lookup; 0,0 if the account is not in an alerting ring).

### Per-seed metrics

| Seed | Detector | P | R | F1 | FPR | TP | FP | FN | TN | Fraud | Eval | Unique scores | Degenerate |
|------|----------|---|---|----|-----|----|----|----|----|-------|------|---------------|------------|
| 42 | isolation_forest_l1_features | 0.625 | 0.139 | 0.227 | 0.0286 | 5 | 3 | 31 | 102 | 36 | 141 | 140 | no |
| 42 | isolation_forest_all_features | 0.625 | 0.139 | 0.227 | 0.0286 | 5 | 3 | 31 | 102 | 36 | 141 | 141 | no |
| 123 | isolation_forest_l1_features | 0.500 | 0.182 | 0.267 | 0.0339 | 4 | 4 | 18 | 114 | 22 | 140 | 140 | no |
| 123 | isolation_forest_all_features | 0.625 | 0.227 | 0.333 | 0.0254 | 5 | 3 | 17 | 115 | 22 | 140 | 139 | no |
| 7 | isolation_forest_l1_features | 0.375 | 0.136 | 0.200 | 0.0407 | 3 | 5 | 19 | 118 | 22 | 145 | 144 | no |
| 7 | isolation_forest_all_features | 0.375 | 0.136 | 0.200 | 0.0407 | 3 | 5 | 19 | 118 | 22 | 145 | 145 | no |
| 2026 | isolation_forest_l1_features | 0.750 | 0.120 | 0.207 | 0.0211 | 6 | 2 | 44 | 93 | 50 | 145 | 144 | no |
| 2026 | isolation_forest_all_features | 0.750 | 0.120 | 0.207 | 0.0211 | 6 | 2 | 44 | 93 | 50 | 145 | 144 | no |
| 99 | isolation_forest_l1_features | 0.750 | 0.120 | 0.207 | 0.0215 | 6 | 2 | 44 | 91 | 50 | 143 | 143 | no |
| 99 | isolation_forest_all_features | 0.750 | 0.120 | 0.207 | 0.0215 | 6 | 2 | 44 | 91 | 50 | 143 | 143 | no |

### Score degeneracy

Unique IsolationForest scores per seed (L1 / L1+structural): 42: 140/141, 123: 140/139, 7: 144/145, 2026: 144/144, 99: 143/143.
Degenerate (unique < 5 or unique < k) on any seed: L1=False, L1+structural=False.

### Aggregated vs GraphDrift (mean ± std [min, max], n=5)

| Detector | Precision | Recall | F1 | FPR |
|----------|-----------|--------|----|-----|
| layer1 (GDI / Mahalanobis, existing) | 0.675 ± 0.168 | 0.158 ± 0.027 | **0.251 ± 0.027** | 0.023 ± 0.010 |
| isolation_forest_l1_features | 0.600 ± 0.163 [0.375, 0.750] | 0.139 ± 0.025 [0.120, 0.182] | 0.222 ± 0.027 [0.200, 0.267] | 0.029 ± 0.008 [0.021, 0.041] |
| fusion (percentile L1+L2, existing) | 0.620 ± 0.117 | 0.188 ± 0.048 | **0.284 ± 0.051** | 0.035 ± 0.004 |
| isolation_forest_all_features | 0.625 ± 0.153 [0.375, 0.750] | 0.149 ± 0.045 [0.120, 0.227] | 0.235 ± 0.056 [0.200, 0.333] | 0.027 ± 0.008 [0.021, 0.041] |

† Cite these **sklearn-default** Isolation Forest rows (`n_estimators=100`, `max_samples='auto'`, `max_features=1.0`). Sensitivity sweep (300 trees, `max_samples=64`, `max_features=0.7`): IF-L1 F1 **0.215–0.222**, IF-all **0.235–0.249**; GraphDrift stays ahead. Do not cite the best-of-sweep cell. See [Isolation Forest sensitivity](#isolation-forest-sensitivity).

### Honest read

Layer 1 (Mahalanobis) beats Isolation Forest on the same 8 features (F1 0.251 vs 0.222, Δ=0.029). The gap is modest — about one Layer-1 F1 standard deviation (0.027) — but the sign is consistent: GDI never loses a seed to IF-L1 (ties on 123 and 99). Hypothesis: remaining features are still correlated (counts vs degrees, fan_ratio vs in/out); Mahalanobis uses the inverse covariance, Isolation Forest splits axis-aligned and cannot represent that ellipsoid as cheaply. Hand-designed percentile fusion beats Isolation Forest given the same L1+L2 columns (F1 0.284 vs 0.235, Δ=0.049). Structural signal is sparse (most accounts have hub_concentration=external_edge_ratio=0 unless they sit in a Louvain ring that cleared Layer 2's own gates); percentile fusion is built to let a rare ring_risk dominate, while Isolation Forest treats those two columns as two more random-split features. Isolation Forest does not gain meaningfully from concatenating the two structural columns (ΔF1=+0.013, within 0.02). The extra signal is concentrated on rare ring members; percentile fusion is built to let that tail dominate, IF is not.

Reproduce: `python -m evaluation.eval_isolation_forest`.

<!-- /isolation-forest -->

## Isolation Forest sensitivity

Original eval used sklearn **defaults** except `random_state=seed` and `n_jobs=1`:
`n_estimators=100`, `max_samples='auto'` (min(256, n) ≈ all ~140 rows), `max_features=1.0`,
`contamination='auto'` (labels unused; we rank `-score_samples` with the same top-5% cut).
This is a 4-config sensitivity check, not a search to beat GraphDrift.

### Sweep (mean F1, n=5 seeds)

| Config | n_estimators | max_samples | max_features | IF-L1 mean F1 | IF-all mean F1 | vs GDI 0.251 | vs fusion 0.284 |
|--------|--------------|-------------|--------------|---------------|----------------|--------------|-----------------|
| `sklearn_default` (cite default) | 100 | auto | 1.0 | 0.222 | 0.235 | +0.029 | +0.049 |
| `n_estimators_300` | 300 | auto | 1.0 | 0.222 | 0.242 | +0.029 | +0.042 |
| `max_samples_64` | 100 | 64 | 1.0 | 0.222 | 0.249 | +0.029 | +0.035 |
| `max_features_0.7` | 100 | auto | 0.7 | 0.215 | 0.235 | +0.036 | +0.049 |

IF-L1 F1 range across configs: **0.215–0.222** (median 0.222).
IF-all F1 range: **0.235–0.249** (median 0.238).
Cite **`sklearn_default`** (most defensible: published sklearn defaults, not the best-of-sweep).

### Reproducibility (seed 42, sklearn_default, twice)

Selected-set identical: **True**. Score arrays allclose: **True**.
L1 F1 both runs: 0.227273 / 0.227273. All-features F1: 0.227273 / 0.227273.

### Recommendation for §4.4

Cite Isolation Forest at sklearn defaults: L1 F1 **0.222**, L1+structural F1 **0.235** (GraphDrift Layer 1 **0.251**, fusion **0.284**). The GraphDrift edge holds across the sweep: even the best IF-L1 (0.222) stays 0.029 below GDI, and the best IF-all (0.249) stays 0.035 below fusion. Range is tight (L1 0.215–0.222; all 0.235–0.249), so the original +0.029 / +0.049 is not an artifact of n_estimators=100. Do not replace the cited IF row with the best-of-sweep number.

Reproduce: `python -m evaluation.eval_isolation_forest_sweep`.

<!-- /isolation-forest-sweep -->

## Adversarial evaluation (parameter-aware evasion)

Attackers are assumed to know the 15/60-minute windows, hub-concentration 
ring scoring, `MIN_RING_MEMBER_COUNT=4`, and the top-5% alert budget. 
This is **not** the existing slow-drip generator: each variant is built to 
break a specific detector assumption. Background is a seeded normals-only 
offline trace (`include_attacks=False`, seed 20260816, 75 min) plus 
5 labeled instances of each variant. Pipeline: multi-scale fusion + 
peripheral cascade. Instance is **caught** if any participant is flagged.

Snapshot: `snapshots/adversarial_eval.db`. as_of=`2026-08-16T13:15:00`. 
Scored 15m=357, scored 60m=457. 
Shipped hybrid flags=222 (union of per-scale top-k, n=40, peripheral 182).

Instance is **caught** if any participant is flagged. **Shipped multi-scale** is the union of independent top-5% cuts at 15m and at 60m (`compute_fused_scores_multiscale`), plus the 15m peripheral cascade. A previous max-then-global-cut merge was removed: 15m and 60m percentiles are not comparable.

| Variant | n | Union fusion | Peripheral-only | Hybrid (union+peri) | Evade both scales |
|---------|---|--------------|-----------------|---------------------|-------------------|
| `standard` | 5 | 5/5 (100.0%) | 5/5 (100.0%) | 5/5 (100.0%) | 0/5 (0.0%) |
| `straddle_15` | 5 | 5/5 (100.0%) | 5/5 (100.0%) | 5/5 (100.0%) | 0/5 (0.0%) |
| `straddle_60` | 5 | 0/5 (0.0%) | 0/5 (0.0%) | 0/5 (0.0%) | 5/5 (100.0%) |
| `diluted_hub` | 5 | 0/5 (0.0%) | 0/5 (0.0%) | 0/5 (0.0%) | 5/5 (100.0%) |
| `minimal_ring` | 5 | 5/5 (100.0%) | 5/5 (100.0%) | 5/5 (100.0%) | 0/5 (0.0%) |

Union fusion = independent top-5% at 15m ∪ 60m. Peripheral-only = a spoke 
flagged by the cascade without its instance already in the union set. 
Hybrid = union ∪ peripheral. Fusion-15m / fusion-60m alone: 
- `standard`: 15m 5/5, 60m 0/5.
- `straddle_15`: 15m 5/5, 60m 0/5.
- `straddle_60`: 15m 0/5, 60m 0/5.
- `diluted_hub`: 15m 0/5, 60m 0/5.
- `minimal_ring`: 15m 5/5, 60m 0/5.

### Hub-dilution vs peripheral cascade

5 diluted_hub instances. Co-mules in union fusion: 0/5. Instances with any spoke in the peripheral pass: 0/5. 
Peripheral only fires for 1–2 tx neighbors of an already-selected hub; dilution keeps every co-mule out of that hub set, so spokes have nothing to attach to.

- `diluted_hub:0`: fusion hubs=none, peri spokes=0, hybrid_any=False
- `diluted_hub:1`: fusion hubs=none, peri spokes=0, hybrid_any=False
- `diluted_hub:2`: fusion hubs=none, peri spokes=0, hybrid_any=False
- `diluted_hub:3`: fusion hubs=none, peri spokes=0, hybrid_any=False
- `diluted_hub:4`: fusion hubs=none, peri spokes=0, hybrid_any=False

### Missed instances (hybrid)

**diluted_hub:0** (`diluted_hub`): 21 members, 21 visible in 15m, 21 visible in 60m. Hybrid flagged none.

- Hub `yastitandon@ybl`
  - 15m fusion: fused=1.879 rank=352/357 gdi=0.698 (pct=0.528) ring=0.000 (pct=0.223) hub_conc=None ring_n=None
    features: in_degree=4.0000, out_degree=4.0000, in_count=4.0000, out_count=4.0000, velocity=0.5333, amount_entropy=1.5000, counterparty_diversity=1.0000, fan_ratio=0.8000, burstiness=0.5824
  - 60m fusion: fused=2.308 rank=129/457 gdi=1.273 (pct=0.908) ring=0.000 (pct=0.015) hub_conc=None ring_n=None
    features: in_degree=4.0000, out_degree=4.0000, in_count=4.0000, out_count=4.0000, velocity=0.1333, amount_entropy=1.5000, counterparty_diversity=1.0000, fan_ratio=0.8000, burstiness=0.5824
  - multi-scale: **not scored** (below min_tx or absent).
  - Louvain 15m: in_window=True hub_conc=0.38095238095238093 members=21 named_hub=True
  - Louvain 60m: in_window=True hub_conc=0.38095238095238093 members=21 named_hub=True
  - raw 15m vector: in_degree=4, out_degree=4, in_count=4, out_count=4, velocity=0.5333, amount_entropy=1.5000, counterparty_diversity=1.0000, fan_ratio=0.8000, burstiness=0.5824
  - raw 60m vector: in_degree=4, out_degree=4, in_count=4, out_count=4, velocity=0.1333, amount_entropy=1.5000, counterparty_diversity=1.0000, fan_ratio=0.8000, burstiness=0.5824

  Why: hub role split across 3 co-mules (each in_degree=4, out_degree=4 including one consolidation edge). Louvain hub_concentration=0.381 in a 21-member community vs ~1.00 for a single-hub 9+9 star. 15m fused=1.879 — well below the top-5% cut (control hubs are ~4.26 with hub_concentration=1.0).

**diluted_hub:1** (`diluted_hub`): 21 members, 21 visible in 15m, 21 visible in 60m. Hybrid flagged none.

- Hub `npatil@paytm`
  - 15m fusion: fused=1.864 rank=353/357 gdi=0.697 (pct=0.522) ring=0.000 (pct=0.223) hub_conc=None ring_n=None
    features: in_degree=4.0000, out_degree=4.0000, in_count=4.0000, out_count=4.0000, velocity=0.5333, amount_entropy=1.5613, counterparty_diversity=1.0000, fan_ratio=0.8000, burstiness=0.5824
  - 60m fusion: fused=2.286 rank=132/457 gdi=1.273 (pct=0.899) ring=0.000 (pct=0.015) hub_conc=None ring_n=None
    features: in_degree=4.0000, out_degree=4.0000, in_count=4.0000, out_count=4.0000, velocity=0.1333, amount_entropy=1.5613, counterparty_diversity=1.0000, fan_ratio=0.8000, burstiness=0.5824
  - multi-scale: **not scored** (below min_tx or absent).
  - Louvain 15m: in_window=True hub_conc=0.38095238095238093 members=21 named_hub=True
  - Louvain 60m: in_window=True hub_conc=0.38095238095238093 members=21 named_hub=True
  - raw 15m vector: in_degree=4, out_degree=4, in_count=4, out_count=4, velocity=0.5333, amount_entropy=1.5613, counterparty_diversity=1.0000, fan_ratio=0.8000, burstiness=0.5824
  - raw 60m vector: in_degree=4, out_degree=4, in_count=4, out_count=4, velocity=0.1333, amount_entropy=1.5613, counterparty_diversity=1.0000, fan_ratio=0.8000, burstiness=0.5824

  Why: hub role split across 3 co-mules (each in_degree=4, out_degree=4 including one consolidation edge). Louvain hub_concentration=0.381 in a 21-member community vs ~1.00 for a single-hub 9+9 star. 15m fused=1.864 — well below the top-5% cut (control hubs are ~4.26 with hub_concentration=1.0).

**straddle_60:0** (`straddle_60`): 19 members, 0 visible in 15m, 11 visible in 60m. Hybrid flagged none.

- Hub `jkarpe@paytm`
  - 15m fusion: **not scored** (below min_tx or absent).
  - 60m fusion: fused=3.577 rank=52/457 gdi=1.167 (pct=0.846) ring=3.000 (pct=0.584) hub_conc=1.0 ring_n=11
    features: in_degree=5.0000, out_degree=5.0000, in_count=5.0000, out_count=5.0000, velocity=0.1667, amount_entropy=1.8464, counterparty_diversity=1.0000, fan_ratio=0.8333, burstiness=0.5322
  - multi-scale: **not scored** (below min_tx or absent).
  - Louvain 15m: in_window=False hub_conc=None members=None named_hub=None
  - Louvain 60m: in_window=True hub_conc=1.0 members=11 named_hub=True
  - raw 60m vector: in_degree=5, out_degree=5, in_count=5, out_count=5, velocity=0.1667, amount_entropy=1.8464, counterparty_diversity=1.0000, fan_ratio=0.8333, burstiness=0.5322

  Why: 15m sees 0 participants (hub often absent). 60m sees an incomplete star (11 members, in_degree/out_degree ≈ 5/5, hub_concentration=1.0 on a 11-node leftover community). 60m fused rank 52/457 is outside the top-5% budget (k=23). High-activity pool accounts with 20–30 txs over the hour take the slow-scale slots.

**straddle_60:1** (`straddle_60`): 19 members, 0 visible in 15m, 11 visible in 60m. Hybrid flagged none.

- Hub `gviswanathan@okicici`
  - 15m fusion: **not scored** (below min_tx or absent).
  - 60m fusion: fused=3.561 rank=53/457 gdi=1.160 (pct=0.840) ring=3.000 (pct=0.584) hub_conc=1.0 ring_n=11
    features: in_degree=5.0000, out_degree=5.0000, in_count=5.0000, out_count=5.0000, velocity=0.1667, amount_entropy=1.6855, counterparty_diversity=1.0000, fan_ratio=0.8333, burstiness=0.5322
  - multi-scale: **not scored** (below min_tx or absent).
  - Louvain 15m: in_window=False hub_conc=None members=None named_hub=None
  - Louvain 60m: in_window=True hub_conc=1.0 members=11 named_hub=True
  - raw 60m vector: in_degree=5, out_degree=5, in_count=5, out_count=5, velocity=0.1667, amount_entropy=1.6855, counterparty_diversity=1.0000, fan_ratio=0.8333, burstiness=0.5322

  Why: 15m sees 0 participants (hub often absent). 60m sees an incomplete star (11 members, in_degree/out_degree ≈ 5/5, hub_concentration=1.0 on a 11-node leftover community). 60m fused rank 53/457 is outside the top-5% budget (k=23). High-activity pool accounts with 20–30 txs over the hour take the slow-scale slots.

### Honest summary

- **Harness:** standard 9+9 hybrid recall 5/5 (100.0%); fusion-15m 5/5. The union-of-per-scale-top-k merge is the shipped pipeline.
- **Window-straddle 15m does not evade the fast scale:** fusion-15m 5/5; hybrid 5/5. Half of 9+9 is still a star. 0/5 evaded 15m and were caught only at 60m (fusion-60m 0.0%).
- **Window-straddle 60m:** 5/5 evaded both scales. Hybrid 0/5. Open. Root cause is NOT window alignment (windows are already sliding, `as_of - window`). A ~3-min burst simply does not rank on a 60-min scale: these hubs sit at velocity-rank 160-165/185, below ordinary accounts doing 35-39 tx/hour. Extending the peripheral cascade to 60m was tried and does not help - the hub is never flagged, so the cascade has nothing to attach to. Closing this needs scale-invariant burst features (future work).
- **Hub dilution:** hybrid 0/5; union fusion 0/5; peripheral-only 0/5. Open in the default configuration: no co-mule clears either scale's top-k, so the cascade has no hub to attach 1-tx senders/receivers to. Optional co-hub scoring closes this (5/5) at a measured accuracy cost - see 'Co-hub scoring (optional, off by default)'.
- **Minimal 4+4:** hybrid 5/5; fusion-15m 5/5. mule+4+4 = 9 nodes, still ≥ `MIN_RING_MEMBER_COUNT=4`.
- Open vectors in this configuration: `straddle_60` and `diluted_hub`. Co-hub scoring closes `diluted_hub` but is off by default. The old max-then-global-cut is removed from `fusion.py`.

Reproduce: `python -m evaluation.generate_adversarial_snapshot` then 
`python -m evaluation.eval_adversarial`.

<!-- /adversarial -->
## Performance benchmarks

Wall-clock via `time.perf_counter()` inside `run_detection_cycle(..., profile=True)`. 
Each cycle measurement: 2 warmup + 20 timed runs on the baseline snapshot (12 timed runs at 500/1k accounts, 4 at 2.5k/5k). Alerts/score history are cleared between runs so persist cost is the create path. Sub-phases: features, Layer 1 (Mahalanobis), Layer 2 (graph + Louvain + hub concentration), 
fusion/merge (per-scale percentiles + union top-k), peripheral cascade, persist (expire + 
score history + alert writes, including explanation assembly).

### Detection cycle latency — baseline `multiseed_seed42.db`

Snapshot: 998 accounts, 7449 txs, active 15m=199, active 60m=424.

| Phase | mean / median / p95 / p99 (ms) |
|-------|--------------------------------|
| total cycle | 547 / 513 / 633 / 923 |
| feature extraction (15m+60m) | 47 / 43 / 72 / 92 |
| Layer 1 Mahalanobis (15m+60m) | 1 / 1 / 2 / 2 |
| Layer 2 Louvain + hub conc. (15m+60m) | 100 / 92 / 131 / 150 |
| fusion + union merge | 2 / 2 / 2 / 3 |
| peripheral cascade | 339 / 316 / 426 / 616 |
| alert/history persist | 53 / 48 / 66 / 103 |

### Scalability vs active-account count

| Target pool | Active 15m | Active 60m | Txs | Alerts | Cycle mean (ms) | Cycle p95 (ms) | Persist mean (ms) | Layer 2 mean (ms) |
|-------------|------------|------------|-----|--------|-----------------|----------------|-------------------|-------------------|
| 500 | 372 | 506 | 1037 | 87 | 396 | 441 | 51 | 64 |
| 1000 | 684 | 1002 | 2038 | 152 | 799 | 838 | 85 | 163 |
| 2500 | 1602 | 2486 | 5036 | 320 | 2305 | 2537 | 202 | 717 |
| 5000 | 3162 | 4927 | 10035 | 603 | 7125 | 10465 | 558 | 2711 |

Log-log slope of mean cycle time vs 15m-active accounts: **1.34** (somewhat worse than linear (typical of graph/community work)). Slope 1 is linear. The superlinear term is **persist** (per-alert `build_explanation` + SQLite commits), not Louvain: alert count tracks the union top-percentile of active accounts. A 10,000-account target was not fully timed; at 5,000 accounts mean cycle already exceeds the 45s live interval.

At 5,000 accounts, a **steady-state** pair of cycles that did not clear existing alerts still took **79.6s / 74.3s** (0 new alert actions) vs **74.9s** cold-create (526 CREATEs). Persist stayed ~69–74s in both cases: `build_explanation` plus score-history writes, not the INSERT of new Alert rows, dominate. Production would not get a free pass after the first tick without changing those paths.

### Transaction ingest throughput (write path only)

5000 legitimate simulator writes, pool=2000.

- Commit-per-tx (live simulator path): **446 tx/s** (11.22s).
- Batched commit every 100: **334 tx/s** (14.95s).
- Demo loop injects 1 event / 2s (**0.5 events/s**); ingest is not the demo bottleneck.

UPI nationally peaks at tens of thousands of tx/s; a single bank still sees hundreds to thousands tx/s at busy hours. SQLite's measured commit-per-tx rate substantiates the paper's prototype-not-production claim if ingest were required at bank scale on this process.

### End-to-end alert latency

Live detection interval = **45s**. Observed cycle compute on the e2e fixture = **1851 ms**. Hub `qdey@paytm` alerted=True.

- Theoretical **best** (attack completes just before a cycle): ≈ **1851 ms** (compute only).
- Theoretical **worst** (just after a cycle starts): ≈ **46.9 s** (45s wait + compute).
- Expected wait if arrival is uniform in the interval: ≈ **24.4 s**.

Immediate post-write cycle (best-case test) took **1858 ms** and produced the hub alert, matching the compute-bound best case. A 2s mid-interval wait then a cycle measured **3531 ms** write-to-alert (hub alerted=True), consistent with wait + compute. The 45s interval, not SQLite, dominates analyst-visible delay at current demo scale. At the 5k-account constructed snapshot, cycle compute already exceeds 45s, so the worst case becomes 2×cycle (overlap / skipped ticks) rather than interval+compute.

### Honest read

At the frozen baseline snapshot, mean cycle **547 ms** (p95 **633 ms**) is well under the **45s** loop, so cycles do not overlap. Persist is the largest slice (**53 ms** mean, ~10% of the cycle) because this bench clears alerts and re-creates them, including `build_explanation`. Layer 2 is **100 ms** mean. At the largest generated scale (15m-active=3162), mean cycle is **7.13s**. Scale exponent (log-log) **1.34**. Ingest at **446 commit-per-tx/s** is far above the 0.5 event/s demo, and far **below** national UPI. SQLite is a measured prototype ceiling, not a theoretical aside. End-to-end alert delay is **interval-dominated (~22s typical, ~45s+compute worst)**, not compute-dominated at current graph size. Performance is a **demo-scale strength** and a **production-scale limitation** — both belong in the paper.

Reproduce: `python -m evaluation.bench_perf`.

<!-- /perf-bench -->
## Analyst-feedback calibration (control loop on alert top-share)

Closed control loop on the **alert top-share**, not a post-hoc filter. Confirmed
rate = `confirmed / (confirmed + false_positive)` over the last N judged alerts
(`new`, `reviewing` and `auto_closed` excluded, matching Reports). Band
**60-85%**: below tightens, above loosens. Fixed **±0.5 pp** per step, never
proportional. Two consecutive same-side ticks are required before any step, and
the auto clamp is **2-15%**. Default **off**; the live tick runs from
`app/main.py`, never inside `run_detection_cycle`, so evaluation and perf
numbers stay comparable.

### Synthetic stress streams (40 cycles, start 5.0%)

Five adversarial input streams against the controller alone. All pass, with no
oscillation reversals and no clamp overshoot.

| Stream | Settled | Steps | First step | Behaviour |
|--------|---------|-------|-----------|-----------|
| `realistic` (p(confirm)=0.64) | 5.0% | 0 | - | in band, never moves |
| `degenerate_all_confirm` | 15.0% | 20 | cycle 3 | walks up, holds at clamp |
| `degenerate_all_false_positive` | 2.0% | 6 | cycle 3 | walks down, holds at clamp |
| `adversarial_noisy` (100% <-> 0% each tick) | 5.0% | 0 | - | side gate absorbs the flip |
| `sparse` (1 judgment / 2 ticks) | 5.0% | 0 | - | no move until n>=20 (cycle 39) |

Reproduce: `python -m evaluation.bench_calibration` -> `data/calibration_stress.json`.
Re-run on 2026-09-09 reproduced the committed JSON **byte-identically**.

### Closed loop against the real detector - negative result

`evaluation/closed_loop_calibration.py` drives the real `run_detection_cycle`
and `tick_live_detection_cycle` over a seeded simulator trace, judging each new
alert from synthetic-attack transactions on that alert's account inside that
alert's own scoring window.

| Cycles | Adjustments | Final top-% | Clamp-riding cycles | Equilibrium |
|--------|-------------|-------------|---------------------|-------------|
| 60 | 14 | 12.0% | 0 | `not-settled` |
| 120 | 20 | 15.0% | 37 | `clamp-bound` |

**The loop does not find an interior equilibrium on this trace — it saturates at
the upper clamp.** Every applied step is `loosen`: the synthetic ground truth
returns a confirmed rate of **0.96-1.00** at every tick, permanently above the
85% band, so the law loosens on every evaluation until the 15% clamp stops it
(reached at cycle 84, then held for 37 cycles).

This is the controller obeying its documented law, not a controller bug — high
precision *should* loosen.

**The confirmation logic is not at fault, and this is not a reason to distrust
it.** Each alert is judged strictly inside its own scoring window
(`detected_at - detection_window <= timestamp <= detected_at`), and that scoping
is correct and covered by a dedicated regression test:
`test_ground_truth_uses_the_alerts_actual_scoring_window` builds one attack
transaction 30 minutes before `detected_at` and asserts it is **excluded** for a
15-minute alert and **included** for a 60-minute one. Window-scoped confirmation
works as intended.

The cause is upstream of confirmation, in the **trace's attack-injection
design**. `generate_offline_trace` rolls a fresh `random.random()` against
constant `MULE_ATTACK_PROBABILITY` (0.015) and `SLOW_DRIP_ATTACK_PROBABILITY`
(0.005) on **every** step. The injection rate never varies with time and the
trace has no organic quiet period, so fraud density stays roughly constant for
the whole evaluated horizon — measured at 0.0142 / 0.0058 (fast / slow-drip per
step) over 60 cycles and 0.0133 / 0.0055 over 120. A near-constant supply of
genuine in-window attack activity means a top-share widening from 5% to 15% is
still mostly landing on real fraud, so the confirmed rate stays pinned near
1.00 and the loop is never given the false positives that would push it back
down. There is no restoring force to find because the environment never
supplies one — not because confirmations are decided wrongly.

Testing convergence therefore needs a trace whose fraud density *varies* (a
time-varying or burst-then-quiet injection rate), so that widening the
top-share actually starts admitting benign accounts. Interpreting the present
result as "calibration converges" would be wrong; so would reading it as "the
ground-truth logic can't be trusted." Cite the synthetic stress streams above
for controller stability; cite this section for the limitation of the
closed-loop *trace*.

Note that `test_closed_loop_adjusts_without_riding_a_clamp` stops at 24 cycles
and passes for that reason — the clamp is not reached until cycle 84. The test
name claims more than the test establishes; treat it as a short-horizon
regression, not evidence of convergence.

Reproduce: `python -m evaluation.closed_loop_calibration --cycles 120`.

### Tests

| Test | Covers |
|------|--------|
| `tests/test_calibration.py` (10 tests) | band/step/clamp law, two-tick side gate, DB status exclusion, manual override reset, live tick cadence |
| `tests/test_closed_loop_calibration.py` (2 tests) | ground truth uses the alert's own scoring window (15m vs 60m); short-horizon adjustment without clamp riding |

## Interpretation

- **Synthetic snapshot (cite this):** 15-min fusion F1 = **0.284 ± 0.051** (n=5 seeds, ≥3-tx). Multi-scale **union** fusion F1 = **0.336 ± 0.072**. Hybrid (union+peripheral, all-active) F1 = **0.691 ± 0.065**. Do not cite retired max-merge 0.304 / 0.644 or the historical single-snapshot fusion 0.625.
- **PaySim (corrected timing + rank-based threshold):** fusion F1=0.041 (TP=5, FP=95, FN=141, fraud=146, eval=1989) vs baseline F1=0.000.

**PaySim external validation could not be re-run (pre-existing break).** The
committed `evaluation/data/paysim_eval.db` was built before the
`is_labeled_fraud` column existed, so `run_eval` aborts with
`no such column: transactions.is_labeled_fraud`. The corpus needs reloading
(`python -m evaluation.load_paysim`) before those rows can be refreshed. This
is unrelated to co-hub - and since the default configuration is byte-identical
to the pipeline that produced them, the PaySim figures above still stand.

## Co-hub scoring (optional, off by default)

`diluted_hub` is a real evasion vector, not a hypothetical one. Splitting the
mule role across 3 co-mules puts every individual `hub_concentration` at
**0.381** (8/21) against **1.0** for an equivalent single-hub star, so no
co-mule clears the top-5% budget and the ring passes unflagged: adversarial
recall **0/5**.

A working fix exists and is shipped, **disabled by default**
(`enable_cohub_scoring: false` in `shared/detection_knobs.json`).
`_detect_co_hub` treats a coordinated set of 2-4 similar-degree nodes as one
logical hub and scores their combined edge share. Two gates keep it specific
rather than a disguised threshold drop:

- **similarity** - `min >= 0.6 x max` degree, so co-mules splitting a role
  evenly qualify but a hub plus a spoke does not;
- **separation** - `min >= 2 x` the next node's degree, which is what an
  evenly-connected benign community fails: its degrees tail off smoothly
  instead of dropping off a cliff.

It was unit-tested against a genuine single-hub star (correctly rejected - that
case already scores 1.0 and must not be double-counted) and two benign shapes,
an even friend group and a smooth degree tail (both correctly rejected), before
being wired into the pipeline. Enabled, it takes `diluted_hub` from **0/5 to
5/5** with no regression on `standard`, `straddle_15` or `minimal_ring`.

### Why it is not the default

Enabling it costs baseline accuracy across every other measure:

| Measure | Default (off) | Co-hub enabled | Delta |
|---|---|---|---|
| `diluted_hub` adversarial recall | 0/5 | **5/5** | closed |
| multi-seed hybrid F1 | **0.691 ± 0.065** | 0.670 ± 0.068 | -0.021 |
| multi-seed fusion_multiscale F1 | **0.336 ± 0.072** | 0.312 ± 0.047 | -0.024 |
| multi-seed fusion F1 | **0.284 ± 0.051** | 0.244 ± 0.090 | -0.040 |
| multi-seed hybrid FPR | **0.191 ± 0.013** | 0.197 ± 0.036 | +0.006 |
| snapshot `hub_concentration` AUC (scored) | **0.809** | 0.777 | -0.032 |
| snapshot `hub_concentration` AUC (size>=4) | **0.876** | 0.796 | -0.080 |
| IBM fusion F1 | **0.033** | 0.032 | -0.001 |
| IBM fusion FP count | **4,163** | 4,345 | +182 |

**Mechanism of the cost.** `compute_fused_scores` unions GDI-scored accounts
with *ring members*, so raising `hub_concentration` pulls more communities over
`RISK_THRESHOLD` and enlarges the scored universe - on the adversarial snapshot
357 -> 447 scored at 15m (+25%), and the top-5% budget k 40 -> 51. Benign
communities that happen to contain 2-4 similar high-degree nodes score higher
too, which is why `hub_concentration` becomes a *weaker* discriminator
(AUC 0.876 -> 0.796 on ring-sized communities) even though it correctly
identifies the synthetic co-mule rings. IBM shows the same shape: identical
TPs (82), +182 FPs.

**Decision.** Degrading precision on every dataset to close one specific
adversarial variant is a bad trade for this project's primary results, so
co-hub ships as an opt-in mode. The default pipeline uses single-node
`hub_concentration`, and every figure cited in this document is measured with
the flag off. Set `enable_cohub_scoring: true` to prioritise resistance to
hub-dilution over baseline accuracy; the numbers you should then expect are the
right-hand column above.

**Known consequence of leaving it off:** `diluted_hub` remains an open evasion
vector in the default configuration, alongside `straddle_60`.

## Snapshot recall by attack type (fusion) — historical single run only

The following breakdown is for `graphdrift_snapshot_2026-08-12.db` only. Do not treat these percentages as multi-seed means.

> **Correction (2026-09-10).** The `slow_drip` row below reads 1/1 = 100%, which
> is a sample of **one** and was never representative. Re-running
> `multiscale_slow_drip.py` against this same snapshot gives the real figures:
> **60-minute scale alone 7/33 = 21.2%**, **multi-scale union 11/33 = 33.3%**
> (of which 7 are tagged `detection_window=60`, i.e. caught *only* by the slow
> scale — two of them score exactly 0.000 at 15m). Cite 21.2% / 33.3%, never
> the 100%-of-one below. The 60-minute scale is a working, load-bearing
> component: it contributes unique detections that 15m alone cannot make.

| Attack type | In window | Scored (≥3 tx) | Filtered out | Detected | Recall (scored) |
|-------------|-----------|----------------|--------------|----------|-----------------|
| fast_fan_in_fan_out | 90 | 21 | 69 | 9 | 42.9% |
| slow_drip | 1 | 1 | 0 | 1 | 100.0% |
| all_synthetic | 91 | 22 | 69 | 10 | 45.5% |

**Coverage:** 69 of 91 fraud-involved accounts in that window had <3 txs and never entered the scored universe. Fusion's 0.625 F1 is recall on the 22 scored accounts, not on all attack participants.

### Future work: scale-invariant burst features

`straddle_60` remains open. The 60-minute scale ranks a ~3-minute burst by
rate, so 10-12 transactions over a 60-minute denominator lands at velocity-rank
160-165 of 185 - below ordinary accounts doing 35-39 tx/hour. Closing it needs
features that score a burst against a short sub-window even when the outer
window is 60 minutes, so burst shape survives the larger denominator. Not
attempted here: it is a features redesign with its own regression risk, and the
cheaper topology route (peripheral cascade at 60m) was measured and rejected -
it cannot fire because the hub itself is never flagged.

## Limitations

- Snapshot metrics in the first table are one `as_of`; multi-seed means are the robustness claim.
- Layer-1 and fusion share the same top-percentile alert budget, which caps recall when many fraud accounts compete.
- PaySim: eval-only `min_transactions=1`; 12k cap.
- IBM HI-Small is a different evaluation mode (dense-slice, single-window) — see below.

## IBM HI-Small evaluation corpus

**Patterns file:** `HI-Small_Patterns.txt` is included on the HuggingFace mirror (`OsamaMIT/IBM-AML-HI-Small`) and was used to measure real laundering-ring durations (370 labeled typology instances). Median ring duration is **74.7 hours**. Compression is **÷332** so that median sits at ~13.5 compressed minutes (slow-drip analog in the 60-min window).

`ibm_aml_eval.db`: 250,000 txs / 1,058 labeled-fraud txs / densest 48 IBM hours `2022-09-08 02:11` → `2022-09-10 02:11`.

### Evaluation mode — dense-slice, single-window (not temporal-burst-against-background)

This is a **different evaluation mode** from the simulator snapshot and from PaySim. Do **not** read IBM numbers in the same sense as snapshot/PaySim rows in the table above.

| Mode | What the detector sees | Example |
|------|------------------------|---------|
| **Temporal-burst-against-background** (simulator / intended PaySim) | Attacks are localized bursts. A 15-min window contains the ring *plus* surrounding normal traffic from the same period; other windows contain mostly background. Time-relative features (velocity, burstiness) contrast burst vs quiet. | Simulator fan-in/out 2–3 min inside a 15-min slice of ongoing UPI traffic |
| **Dense-slice, single-window** (this IBM subsample) | After ÷332, the entire 48h dense slice occupies **8.67 eval minutes**. There is **1** occupied 15-min bin. **100.0%** of all 250,000 transactions (fraud and legitimate) fall in the single 15-min eval window at `max(timestamp)`. There is no second window of “just background.” | 250k txs, mix of 48h of IBM activity, scored together |

Compressing *less* would spread txs across more windows but would put the 74.7h median ring outside every detection window — the original problem. We accept the single-window mode rather than fake a simulator-like backdrop.

**Limitation — laundering-dense 48-hour slice.** The window was chosen because it is laundering-dense, not randomly sampled. Full HI-Small fraud rate is **~0.10%**; this slice is **~0.42%**. Results are evaluation on a dense slice, not a claim about corpus base rates.

### Layer-1 feature audit (scored accounts, ≥3 tx in the 15-min window)

Compared to snapshot `graphdrift_snapshot_2026-08-12.db` (temporal-burst mode). AUC is Mann–Whitney P(class A > class B); 0.50 = no ranking signal. “Degenerate/weak” = near-constant or AUC < 0.55.

IBM scored: 64605 accounts (703 labeled-fraud, 63902 legit). Snapshot scored: 102 (22 synthetic-fraud, 80 legit).

| Feature | IBM fraud median | IBM legit median | IBM AUC | Snapshot fraud median | Snapshot legit median | Snapshot AUC | IBM verdict |
|---------|------------------|------------------|---------|-----------------------|-----------------------|--------------|-------------|
| burstiness | 0.889 | 0.911 | 0.506 | 0.835 | 0.423 | 0.780 | degenerate/weak |
| velocity | 0.267 | 0.267 | 0.607 | 0.300 | 0.200 | 0.739 | still informative |
| in_degree | 2.000 | 1.000 | 0.674 | 2.000 | 1.000 | 0.551 | still informative |
| out_degree | 2.000 | 1.000 | 0.565 | 3.000 | 2.000 | 0.759 | still informative |
| fan_ratio | 0.500 | 0.500 | 0.563 | 0.500 | 0.333 | 0.564 | still informative |
| amount_entropy | 1.379 | 1.371 | 0.518 | 1.111 | 1.500 | 0.518 | degenerate/weak |
| counterparty_diversity | 0.750 | 0.667 | 0.681 | 1.000 | 1.000 | 0.516 | still informative |

**Burstiness (IBM):** fraud median 0.889 vs legit 0.911, AUC 0.506, 0% of fraud and 0% of legit at 0. does not separate fraud from legit (AUC≈0.5). On the snapshot, burstiness fraud median 0.835 vs legit 0.423, AUC 0.780.

**Velocity (IBM):** fraud median 0.267 vs legit 0.267, AUC 0.607. Velocity is `tx_count / 15` over a window that already contains the entire subsample, so it is a **global degree/count feature**, not a local burst rate. Snapshot velocity AUC 0.739 (fraud median 0.300 vs legit 0.200).

### Layer 2 hub-concentration isolation (formed_recently zeroed)

`formed_recently` fires for every IBM community in this slice (no prior window). To test whether Layer 2 still has a structural signal, ring scores were recomputed from **hub_concentration + external_edge_ratio only** (the 15% recency term set to 0). Accounts not in a Louvain community of size ≥4 get 0.

| Feature | Universe | IBM fraud median | IBM legit median | IBM AUC | Snapshot AUC (same protocol) |
|---------|----------|------------------|------------------|---------|------------------------------|
| hub_concentration | scored (≥3 tx) | 0.058 | 0.060 | **0.526** | **0.809** |
| hub_concentration | size≥4 communities only | 0.185 | 0.196 | **0.519** (lower=fraud) | **0.876** |
| external_edge_ratio | scored (≥3 tx) | 0.077 | 0.060 | 0.572 | 0.537 |
| structural score (hub+external, no recency) | scored (≥3 tx) | 0.144 | 0.152 | **0.528** | **0.814** |
| is community hub | scored (≥3 tx) | 0 | 0 | 0.543 | 0.559 |

On the simulator snapshot, hub-concentration is a real ranking signal (AUC 0.81–0.88). On IBM HI-Small it is not: among ring-sized communities, labeled-fraud accounts have a **slightly lower** hub_concentration median than legit (0.185 vs 0.196). The IBM Layer-2 miss is therefore **not only the single-window `formed_recently` artifact**. A wider real-time span would restore quiet windows for recency, but would not create a hub-concentration ranking that is absent here. **No wider-span reload was run.**

**Mechanism check (FAN-IN / FAN-OUT only — the typologies closest to hub-and-spoke).** 40 of 88 Patterns-file FAN-IN/FAN-OUT instances have ≥1 tx in the densest 48h slice (38 hubs, 120 spokes, all present in `ibm_aml_eval.db`). Fraud-edge fraction = (window txs that match that account’s FAN-IN/FAN-OUT instance edges) / (all window txs involving the account).

| Group | n | p25 | median | p75 | share = 1.0 | share < 0.5 |
|-------|---|-----|--------|-----|-------------|----------------|
| FAN-IN/FAN-OUT **hubs** | 38 | 0.35 | **0.74** | 1.00 | 39.5% | 31.6% |
| FAN-IN/FAN-OUT spokes | 120 | 0.25 | **0.50** | 1.00 | 33.3% | 45.0% |
| all involved | 154 | 0.25 | 0.50 | 1.00 | 35.7% | 40.3% |

Hubs: median 6 window txs, of which median **2** match the labeled instance (the rest of a multi-day fan-out often sits outside this 48h slice). Snapshot synthetic mules are designed so fraud-edge fraction ≈ 1.0.

**Louvain community size (same 15-min scored window / Louvain settings):**

| | n communities | median (all) | median (size≥4) | mean (size≥4) | max |
|--|---------------|--------------|-----------------|---------------|-----|
| IBM | 36,489 | **2** | **5** | 21.9 | 8,117 |
| Snapshot | 35 | **9** | **12.5** | 12.7 | 24 |

What this does and does not support:

- **Legit-volume dilution is only a partial story.** It is not true that typical FAN-IN/FAN-OUT hubs are majority-legitimate: median hub still has **74%** instance edges. About **40%** of hubs are pure (fraction 1.0, like the simulator). About **32%** are majority-other (fraction < 0.5). Dilution exists as a minority/mixed pattern, not as the typical hub.
- **“IBM communities are larger and more diffuse” is not true at the median.** Typical IBM Louvain communities are **smaller** than the snapshot (median 2 vs 9) — mostly dyads below the size-4 ring gate. Ring-eligible communities are also smaller at the median (5 vs 12.5). There **is** a heavy tail (one component of 8,117 vs snapshot max 24) that would crush hub-concentration for accounts stuck in that blob, but that is not the typical community.
- **Honest combined finding:** Layer 2’s hub-concentration feature was tuned on small synthetic stars whose window activity *is* the attack. IBM FAN-IN/FAN-OUT in this slice are truncated multi-day typologies (median 2 instance legs in-window) sitting in a graph of mostly 2-node communities plus a few giant components. That is a **mismatch of graph regime**, not a single clean “dilution” or “bigger clusters” failure. The null AUC stands; we do not claim a wider span would fix it.

Fusion F1 is **below** Layer 1 here (0.033 vs 0.066). Recency cannot help (empty prior window); hub-concentration does not rank IBM laundering accounts even with recency zeroed. Treat fusion numbers as the same single-window protocol, not as evidence that Layer 2 helps on IBM in the simulator sense.

Fusion F1 is **below** Layer 1 here (0.033 vs 0.066). The previous 15-min graph is empty, so recency cannot help; even without recency, hub-concentration does not rank IBM laundering accounts. Treat fusion numbers as the same single-window protocol, not as evidence that Layer 2 helps on IBM in the simulator sense.

### Detector comparison — IBM dense-slice, single-window only

Ground truth: accounts touching `Is Laundering=1` transactions (`is_labeled_fraud`). Same 15-min `as_of=max(timestamp)` protocol as other evals, **interpreted under the mode above**. Not comparable without that qualification to snapshot/PaySim F1.

| Detector | P | R | F1 | FPR | TP | FP | FN | TN | Fraud† | Eval‡ |
|----------|---|---|----|-----|----|----|----|----|--------|-------|
| baseline | 0.000 | 0.000 | 0.000 | 0.0000 | 0 | 0 | 703 | 63902 | 703 | 64605 |
| layer1 | 0.040 | 0.183 | 0.066 | 0.0485 | 129 | 3102 | 574 | 60800 | 703 | 64605 |
| fusion | 0.019 | 0.117 | 0.033 | 0.0651 | 82 | 4163 | 621 | 59739 | 703 | 64605 |

† Fraud = labeled-laundering accounts with ≥3 transactions in the (single) window. ‡ Eval = scored universe.

Reproduce: `python -m evaluation.analyze_ibm_aml_timing` then `python -m evaluation.load_ibm_aml` then `python -m evaluation.run_ibm_aml_eval`. Layer-2 isolation: `python -m evaluation.ibm_aml_hub_isolation`. Dilution / community-size check: `python -m evaluation.ibm_aml_dilution`.

## Reproduce

```bash
cd graphdrift/backend
python -m evaluation.load_paysim
python -m evaluation.freeze_snapshot
python -m evaluation.run_eval --skip-paysim-load --skip-snapshot
python -m evaluation.generate_adversarial_snapshot
python -m evaluation.eval_adversarial
python -m evaluation.bench_calibration
python -m evaluation.closed_loop_calibration --cycles 120
```
