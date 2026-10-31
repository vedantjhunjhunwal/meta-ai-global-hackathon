# Validation protocol (proposed; not yet executed)

**Status: no part of this protocol has been run. No results exist yet.** Nothing here should be quoted as a measured result.

## 1. Questions to answer

1. **Repeatability.** How much do Stetho's features vary between recordings of the *same* healthy machine, by the same phone, from the same marked spot? (This sets the `FLOOR` values in `stetho/baseline.py`.)
2. **Sensitivity.** Do safely induced faults move features beyond that spread, and by how much relative to an accelerometer reference?
3. **Screening performance.** On held-out machines and sessions, how often does Stetho recommend a technician when one is warranted (recall), avoid false alarms (specificity / false-alarm rate), and abstain?
4. **Robustness.** How do phone model, frame rate, angle, distance, background noise, lighting and compression affect 1–3?
5. **Repair verification.** After a fault is removed, do recordings return within baseline spread?
6. **Latency.** Time from WhatsApp message received to reply sent, measured end-to-end.

## 2. Rig and machines

- **Development rig:** a 2-pole 0.75–1.5 kW monoblock centrifugal pump in a closed water loop with a throttling valve (to vary head/flow) and a suction valve (to induce cavitation safely), mounted on a frame with removable shims.
- **Evaluation machines:** at least 6 *different* pumps (≥2 makes, ≥2 sizes, 2-pole and 4-pole), including ≥3 in real farm/workshop installations. **Evaluation machines are never used for threshold tuning.**
- **Reference sensors:** a triaxial IEPE accelerometer (or two uniaxial ones) stud- or magnet-mounted on the motor drive-end and pump bearing housings, sampled at ≥ 25.6 kHz. A tachometer or optical speed sensor. A reference microphone (calibrated class 1/2 SLM) at the phone position.
- **Synchronisation:** a clap/tap marker visible and audible at the start, plus an LED driven from the DAQ's digital output inside the camera view. Align by cross-correlating phone audio with the reference microphone (target error < 5 ms).

## 3. Safety rules (non-negotiable)

- Faults are induced **only with the machine stopped and isolated (lock-out/tag-out)** by a qualified person. Guards stay on. Nobody touches the machine while it runs.
- Phones are mounted on a tripod or support at ≥ 1 m. No hand-held recording near couplings.
- Cavitation is induced by partially closing the suction valve within manufacturer limits, for short periods only.
- Stop criteria: abnormal temperature rise, smoke or smell, accelerometer RMS velocity above the rig's pre-set trip, or any guard compromise.

## 4. Conditions and induced faults

### 4.1 Healthy operation
3 operating points (throttle open / mid / near shut-off within limits) × 2 water levels × 3 days.

### 4.2 Safely induced faults (each at 2 severity levels where possible)

| Fault | How it's induced (machine stopped, LOTO) | Reference evidence expected |
|-------|------------------------------------------|-----------------------------|
| Imbalance | Add a known mass to the fan cowl clamp / coupling | 1× radial velocity increase |
| Looseness / soft foot | Loosen one foot bolt by a set number of turns; add a shim under one foot | 1×, 2×, sub-harmonics, phase change |
| Misalignment (coupled sets) | Shim offset of known size | 2× axial increase |
| Bearing defect | Swap in bearings with seeded outer-race defects (EDM notch) from a supplier | Envelope peak at BPFO |
| Cavitation | Throttle suction | Broadband HF and pressure fluctuation |
| Debris / rubbing | **Not induced** (unsafe to control) | – |

### 4.3 Recording matrix (per condition)
- Phones: ≥ 4 models (2 budget Android, 1 mid Android, 1 iPhone), at 30 fps normal and slow-motion where available.
- Positions: the marked spot (primary), plus ±30° angle and 0.5 m / 1.5 m distance variants.
- Holding: on a support (primary) and hand-held (robustness only).
- Environment: quiet, with a second running pump nearby, with speech, with wind (outdoor installs).
- Lighting: daylight, shade, dusk / indoor lamp.
- Delivery: original file, WhatsApp "video" (re-compressed), WhatsApp "document".
- 3 repetitions per cell, on ≥ 2 different days.

### 4.4 Static-object controls
Record a switched-off pump and a rigid block with the same matrix, to measure the null distribution of `peak_snr_db` and band RMS. This sets `MOTION_DETECT_SNR_DB`.

### 4.5 Repair sequences
For each induced fault: baseline (×3), fault (×3), fault removed (×3), on the same day and again the next day.

### 4.6 Codec characterisation
On the rig, compare each phone's original file vs. WhatsApp-video vs. WhatsApp-document for the same take. Record the picture-type pattern and the visible-motion spectrum (see SCIENCE.md, compression finding).

## 5. Data split

- **Development set:** rig + 2 machines (threshold tuning, floor estimation).
- **Evaluation set:** ≥ 4 different machines, recorded in sessions after thresholds are frozen and the code version is tagged. Split by **machine and by session**, never by clip.
- Labels come from the reference accelerometer analysis and the known induced condition, assigned by someone who has not seen Stetho's output.

## 6. Baselines and ablations

Compare Stetho against:
1. **Trivial baselines:** always "no change"; always "technician"; overall audio RMS change only.
2. **Absolute-only** (no machine-specific baseline): `assessment.absolute_patterns` alone.
3. **Audio-only** vs. **video-only** vs. **both**.
4. **No camera-motion compensation** (raw region motion) vs. compensation with the held-out-floor gate.
5. **No quality gate** (analyse everything).
6. **With vs. without the LLM rewrite** (check that decisions are identical; they must be, by design).
7. 1 vs. 3 vs. 5 baseline recordings.

## 7. Metrics

Per fault type and severity, on the evaluation set:
- **Recall** (technician/watch recommended when a fault is present) and **precision**.
- **False-alarm rate** on healthy recordings (all conditions), reported separately for operating-point changes.
- **Abstention rate** (rerecord + no_reference) and **accuracy when not abstaining**.
- **Repeatability:** within-machine, within-spot feature spread (MAD), ICC across days, and decision agreement across the 3 repetitions.
- **Agreement with reference:** Spearman correlation between feature change and reference vibration change (e.g. visible displacement change vs. accelerometer velocity change at the shaft frequency; envelope prominence vs. reference envelope at BPFO).
- **Repair verification:** fraction of fault-removed sessions classified `back_to_baseline` or `improved`, and the false "back to baseline" rate while the fault persists.
- **Latency:** p50/p95 end-to-end WhatsApp time on a stated server and network.

Report all with 95% confidence intervals (bootstrap over machines). Report every cell in the condition matrix, including failures.

## 8. Acceptance criteria (proposed; to be agreed before the evaluation run)

| Claim | Minimum evidence |
|-------|-----------------|
| "Stetho asks for a re-recording when the recording is unusable" | ≥ 90% of deliberately bad recordings (dark, very shaky, < 6 s, no reference background) get `rerecord`; ≤ 10% of good ones do |
| "Stetho detects change from a machine's own baseline" (**screening**) | On held-out machines at the marked spot and same phone: recall ≥ 0.8 for moderate-severity imbalance, looseness and seeded bearing defects; false-alarm rate ≤ 0.1 on healthy recordings at the baseline operating point; decision repeatability ≥ 0.85 |
| "Stetho supports repair verification" | ≥ 0.8 of fault-removed sessions `back_to_baseline`/`improved`; ≤ 0.1 false `back_to_baseline` when the fault remains |
| "Reply within a minute" | p95 end-to-end ≤ 60 s for 15 s clips on the stated deployment |
| Any **named fault type** in user-facing language | Fault-specific precision ≥ 0.9 and recall ≥ 0.8 on held-out machines across ≥ 3 phones; otherwise keep pattern-plus-hypotheses wording |

## 9. Screening tool vs. diagnostic system

- **Screening tool** (the most this project could claim after passing §8 rows 1–4): it detects *that* a machine has changed relative to its own baseline and recommends an inspection, with known recall, false-alarm and abstention rates, under stated recording conditions. Its output is a referral, not a conclusion.
- **Diagnostic system** (not claimed, and not a near-term goal): it identifies *which* fault is present with validated, fault-specific accuracy across machines, phones and environments, calibrated against reference instruments, ideally with external/independent evaluation and, where relevant, conformity with standards bodies' guidance. That requires the last row of §8 for every named fault, prospective field data, and an independent test set.

## 10. Reporting

Publish the protocol version, code commit, frozen thresholds, full per-condition tables, failure cases (with short clips where consent allows), and every deviation from this plan.
