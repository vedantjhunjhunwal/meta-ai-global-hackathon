# Science, limitations, and what is actually established

## Three kinds of signal, not to be confused

| Signal | What Stetho measures | Units | What it is **not** |
|--------|----------------------|-------|--------------------|
| **Visible displacement** | Motion of tracked image points on the machine relative to the static background, after camera-motion compensation | pixels (image plane, original resolution) | Not mm, not velocity (mm/s), not acceleration (g). Depends on distance, zoom, angle and resolution. Not comparable with ISO 20816 zones. |
| **Audio features** | Spectral shape (band share of total energy), harmonic levels relative to total, mains hum, shaft-speed comb estimate, high-frequency kurtosis/crest, envelope periodicity | dB relative to total, dimensionless ratios, Hz | Not sound pressure level. The phone's AGC, noise suppression, codec and mic response change absolute levels. |
| **Calibrated vibration** | **Not measured by Stetho.** Needs an accelerometer or velocity sensor mounted on the machine. | m/s², mm/s | Used only as the *reference* in the validation protocol. |

## Status of capabilities

### Implemented and tested in this repository (on simulated and unit data)
- Media probe/decode with measured frame timing; quality gate with specific re-record advice.
- Camera-motion compensation by background-referenced similarity transforms, with a **held-out** set of background points as a recording-specific noise floor.
- Aliasing-aware reporting (apparent vs. true frequency) and an audio↔video cross-check.
- Detection of the codec's I/P/B picture-type cycle and accounting for its alias family (see finding below).
- Audio features: mains detection (50/60 Hz), harmonic-comb shaft-speed estimate (with nameplate constraint), gain-robust shape features, impulsiveness and envelope periodicity.
- Machine-specific baseline comparison (robust z-scores with repeatability floors), change grading, operating-condition checks, repair verdict.
- Rule-based assessment with abstention; template explanations (English, Hindi); guarded LLM rewrite; voice note; stabilised motion magnification.
- WhatsApp webhook flow (consent, commands, dedupe, timeouts, provider failures), web UI, CLI, privacy controls.

### Experimental hypotheses (plausible, **not validated on real machines**)
- That relative features from the same phone at the same marked spot are repeatable enough (spread within the `FLOOR` priors in `stetho/baseline.py`) to detect meaningful change.
- That HF kurtosis plus envelope periodicity in phone audio flags rolling-element bearing impacts on small pumps before an owner would notice by ear.
- That broadband impulsive crackle in phone audio is associated with pump cavitation.
- That audio shaft-speed estimates are reliable enough on farms to resolve video aliasing.
- All absolute thresholds in `assessment.ABS` and `quality.THRESHOLDS`. They were tuned on *simulated* data only.

### Planned, not implemented
- Scale reference (printed marker of known size) to convert pixels to mm at the marker plane.
- Rolling-shutter exploitation (row-wise timing for higher effective sampling).
- SAM 3 tracking validated on a GPU deployment; per-component regions (motor vs. pump end).
- Natural-sounding TTS and more languages (Marathi, Tamil, Telugu, Bengali, Swahili …).
- Real-world-tuned thresholds from the validation protocol.

## Important limitation found while building: video compression

With the built-in simulator we rendered a pump vibrating at 48 Hz, ±0.15 px, at 120 fps, with **no camera shake**, and encoded it four ways:

| Encoding | Strongest residual motion peak | Power at 48 Hz (px²/Hz) |
|----------|-------------------------------|-------------------------|
| lossless (`-crf 0`) | 47.8 Hz | 1.6 × 10⁻² |
| intra-only (`-g 1`) | 47.8 Hz | 1.3 × 10⁻² |
| inter, no B-frames | 47.8 Hz | 2.1 × 10⁻⁴ |
| default x264 (B-frames, pattern BBBP) | **12.2 Hz** | ≈ 4 × 10⁻⁴ (no longer the top peak) |

Two effects are visible. **Inter-frame compression suppressed sub-pixel motion** by roughly 80× in power. The **repeating I/P/B pattern (period 4 frames → 30 Hz) leaked motion energy to |48 ± 30k| folded into band**, i.e. 18 and 12 Hz. Phones and WhatsApp use inter-frame codecs, so this will happen on real recordings. What we did:
- `media.frame_types` / `gop_period` measure the picture-type cycle. `motion.alias_family` lists the frequencies where true motion may appear.
- The cross-check reports `agree_codec_ambiguous` (weaker support) when the match needs the codec family.
- The quality report flags the compression cycle and suggests sending the video as a WhatsApp *document* (which avoids re-compression).
- Visible-motion features only enter baseline comparisons when the frame-rate class matches.

This result comes from a simulator with one encoder (x264). Phone HEVC encoders and WhatsApp's transcoder need to be characterised with real recordings (protocol §4.6).

## Second finding: noise-floor ratios are optimistic when you pick the maximum

On simulated **static** scenes, the largest residual-motion peak still reached 5–15 dB above the held-out background floor. Picking the maximum of many noisy spectral bins inflates the ratio. Texture-dependent tracking bias and codec effects also differ between the machine region and the background. Conversely, on simulated *vibrating* machines the background floor itself shows a small peak at the machine frequency (some leakage of machine motion into background tracking near the machine edge, or through the codec), which makes the reported SNR conservative there. We therefore set `MOTION_DETECT_SNR_DB = 18` as an interim gate. It must be re-derived from real static-object control recordings.

## Physical limits

- **Frame rate and aliasing.** At 30 fps, Nyquist is 15 Hz. A 2-pole motor turns at ≈48–49.5 Hz, so its 1× vibration appears folded (e.g. 48 Hz → 12 Hz). Slow-motion modes (120/240 fps) help but often use lower resolution and stronger compression, and some phones store slow-motion as 30 fps playback with stretched time. Stetho measures frame timing instead of trusting metadata, but cannot always detect time-stretched files. Comparisons should therefore use the same phone and mode.
- **Rolling shutter.** Rows are exposed at different times, so fast motion skews the image and frequency content is spread in a row-dependent way. Not modelled.
- **Exposure and motion blur.** Long exposures (dim light) average out high-frequency motion. Hence the brightness checks.
- **Hand shake.** Compensation uses static background points. It fails when the machine fills the frame (no reference), when the background moves (people, water, leaves), or with strong parallax (a close machine against a far wall when the phone rotates). Similarity transforms don't model perspective changes. Resting the phone on a support matters more than any algorithm.
- **Segmentation does not stabilise.** It only says which pixels are machine and which are background.
- **Motion magnification does not prove a fault.** It is a visual aid. It can create halos and amplify codec artefacts.
- **Audio.** Background noise (other pumps, generators, speech, wind, water flow), distance, room reflections and phone audio processing all change the signal. Spectral-shape features reduce gain effects but not noise from other sources. The shaft-speed comb can lock onto a sub-harmonic or onto another machine, especially with a wrong nameplate hint (documented in tests).
- **Unknown operating point.** Load (head, flow), water level, supply voltage and speed all change sound and vibration in healthy machines. Stetho flags shaft-speed differences above 2.5% but cannot see head/flow. Baselines should be recorded at the usual operating condition.
- **Geometry.** Without knowing bearing geometry, envelope peaks cannot be matched to specific bearing defect frequencies. Stetho reports "knocks per shaft turn" and treats non-integer orders as *consistent with* (not proof of) rolling-element defects.

## The simulator is not evidence

`stetho/synth.py` renders textured scenes and soundtracks with known faults so the pipeline can be tested end-to-end. The same author wrote the simulator and the detectors, so passing tests show the **software does what it was designed to do**. They do not show the method works on real pumps. All simulated files carry `comment=STETHO SIMULATED` metadata, and every report and UI view built from them is labelled "SIMULATED INPUT".
