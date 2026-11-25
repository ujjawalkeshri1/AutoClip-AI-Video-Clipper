# Gameplay engine

Where it lives: `backend/autoclip/gameplay/`. The original transcript pipeline is untouched and still
available (`analysis_mode: speech`).

## Stages (gameplay mode)

1. **prepare**: extract 16 kHz mono audio (existing code).
2. **analyze** (`signals.py`): audio and video signals on a shared 0.5 s timeline, cached in `work/<job>/gameplay/signals.npz`.
3. **candidates** (`candidates.py`): fuse signals, find peaks, expand to windows, categorise, merge duplicates, snap edges.
4. **transcribe** (`speech.py`): faster-whisper only around candidates (or the whole video in Quality mode). No speech is fine.
5. **score + describe** (`scoring.py`, `metadata.py`, optional `vision.py`).

Clips are stored as `candidate`. Rendering happens when you generate a clip.

## Signals

| Channel | Meaning | Source |
|---|---|---|
| motion | object motion (flow residual) or raw frame change, whichever is larger | OpenCV Farneback at 160 px + brightness-normalised frame difference |
| burst | motion above the preceding 4 s | derived |
| stop | sustained movement that drops away abruptly (possible crash) | derived from frame difference |
| flash, warm | brightness spike, fire-coloured pixels | per-frame colour statistics |
| lights | red and blue present together within ~2.5 s | colour statistics |
| energy | loudness above this video's baseline | RMS in dB |
| impact | low-frequency (20-160 Hz) onset | STFT |
| gunfire | rapid sharp broadband onsets, discounted from impact | spectral flux |
| siren | tonal peak in 500-1800 Hz that sweeps | STFT |
| shout, laugh | voice-band loudness with bright spectrum (not tonal), bursty voice-band energy | heuristics |
| cut | histogram difference between frames | cheap scene-cut detection |

Each channel is `clip((x - baseline) / span)` where the baseline is the video's own 70th percentile (never
below an absolute floor) and the span is fixed. So sustained activity that is normal for a video scores low,
and a single huge event does not squash moderate ones.

**Fusion** is a noisy-OR: `salience = 1 - prod(1 - w_c * channel_c)`. One strong channel is enough, which is
what lets silent events surface.

**Peaks** need a height above `max(0.30, 85th percentile)` and prominence 0.10, at least 8 s apart. Windows
extend while salience stays above 40% of the peak, plus a per-category lead-up and aftermath (for example crash
7 s / 5 s, police chase 8 s / 6 s), clamped to 20-90 s (configurable). Peaks within 15 s are one event. Distinct
moments with overlapping context are split at the calm between them.

**Edges** snap to sentence boundaries where speech exists, otherwise to calm points and scene cuts, never
before the event and never right after it.

## Categories

Rule-based over the evidence, specific labels before generic ones:

* EXPLOSION: flash/fire tones with a low-frequency impact.
* CRASH: abrupt stop or violent burst, with or without an impact sound (without one the explanation says it could be braking or a pause).
* POLICE_CHASE: light or siren cues plus sustained motion.
* SHOOTOUT: gunfire-like onsets.
* STREAMER_REACTION / FUNNY: shout-like or laughter-like audio; FUNNY needs low action.
* SURPRISE: a large jump from a calm stretch.
* ACTION: fallback.
* NPC_INTERACTION and MISSION_HIGHLIGHT: only from commentary keywords or the vision model; the sensors alone cannot tell these apart.

Commentary and the vision model can relabel generic detections and raise confidence. Low-confidence labels
get generic titles rather than confident claims.

## Score (0-100)

Six factors, each 0-1, computed independently, then `score = sum(weight_i * factor_i) / sum(weights) * 100`.
Default weights 25/20/15/15/15/10 (any relative values in Settings or `config.yaml`).

| Factor (default points) | Computed from |
|---|---|
| Entertainment value (25) | peak salience and its rank among candidates, how many kinds of evidence agree, reaction, (vision interest if on) |
| Unexpectedness (20) | contrast with the calm before it, how fast it rose, how rare that intensity is in this video |
| Action and intensity (15) | motion, impacts/gunfire, flashes/lights, energy |
| Streamer reaction (15) | shout/laughter indicators across the clip, excitement of what was said |
| Storytelling (15) | build-up present, visible aftermath, clean boundaries, sensible length |
| Engagement (10) | early hook (first 3 s), pacing, visual variety, reaction/impact presence |

Silent clips cannot earn the reaction points, so a purely visual event tops out below 100. This is intended.
Every export's JSON stores the factor values and points, and states that the score is not a view prediction.

## Optional AI reasoning

* **Vision** (`vision.py`): top N moments, 3 keyframes at 512 px to an Ollama vision model; returns labels, a
  one-sentence description, interest 0-10, and a truthful title. Skips cleanly if Ollama or the model is missing.
* **LLM refine**: the active provider writes titles and a hook from the evidence only. It does not change the score,
  except that an interest rating, when present, is one input to two factors.

## Framing (`framing.py`)

`detect_hud` samples 24 low-res frames: pixels with low temporal variance and strong edges while the scene
moves are HUD. `auto` uses Whole frame over blur if more than 35% of HUD pixels would fall outside the 9:16
window, or if the action spreads wider than 80% of the window; otherwise Follow action. Follow action
smooths the motion centroid with a dead zone and a speed cap to avoid whip-pans.

## Not yet done / ideas

* Per-game sensitivity calibration from labelled footage.
* A trained audio event classifier in place of the heuristics.
* Running Whisper on OpenVINO (needs a different backend than faster-whisper).
