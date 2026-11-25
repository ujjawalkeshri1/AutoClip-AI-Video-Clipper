"""Gameplay highlight engine: signals -> candidates -> speech -> score -> metadata.

Order matters for cost on a laptop:
  1. cheap audio + motion signals over the whole video (cached),
  2. candidate windows from those signals,
  3. speech recognition only around candidates (or the whole video if asked),
  4. scoring and metadata,
  5. optional vision / LLM refinement on the best few.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

from ..config import Settings
from ..pipeline.transcript import Transcript
from . import candidates as C
from . import metadata, scoring, signals, speech, vision

log = logging.getLogger(__name__)

#: (stage, fraction 0..1, message)
Progress = Callable[[str, float, str], None]


@dataclass
class AnalysisResult:
    candidates: list[C.Candidate]
    transcript: Transcript
    notes: list[str] = field(default_factory=list)
    duration_s: float = 0.0


def _save_signals(path: Path, audio: signals.AudioSignals, motion: signals.MotionSignals | None) -> None:
    arrays = {f"a_{k}": v for k, v in audio.channels.items()}
    if motion is not None:
        arrays.update({f"m_{k}": v for k, v in motion.channels.items()})
    np.savez_compressed(path, duration=audio.duration_s, fps=motion.sample_fps if motion else 0.0, **arrays)


def _load_signals(path: Path) -> tuple[signals.AudioSignals, signals.MotionSignals | None]:
    z = np.load(path)
    a = signals.AudioSignals(float(z["duration"]), {k[2:]: z[k] for k in z.files if k.startswith("a_")})
    mch = {k[2:]: z[k] for k in z.files if k.startswith("m_")}
    m = signals.MotionSignals(float(z["duration"]), mch, float(z["fps"])) if mch else None
    return a, m


def analyze(
    *, video: Path | None, wav: Path, duration_s: float, settings: Settings, work_dir: Path,
    on_progress: Progress | None = None, cancelled: Callable[[], bool] | None = None,
    provider=None, detection_config=None,
) -> AnalysisResult:
    """Run the full gameplay analysis and return scored, described candidates."""
    g = settings.gameplay
    emit = on_progress or (lambda *_: None)
    notes: list[str] = []
    work_dir.mkdir(parents=True, exist_ok=True)
    cache = work_dir / "signals.npz"

    # ---- 1. signals -------------------------------------------------------
    if cache.exists():
        audio, motion = _load_signals(cache)
        # Rerun if the cache predates a video that now exists, or a different sampling rate.
        if (video is not None and motion is None) or (motion is not None and motion.sample_fps != g.sample_fps):
            cache.unlink()
    if not cache.exists():
        audio = signals.analyze_audio(wav, cancelled=cancelled,
                                      on_progress=lambda f: emit("analyze", 0.30 * f, "Analysing audio"))
        motion = None
        if video is not None:
            motion = signals.analyze_motion(
                video, duration_s=duration_s, sample_fps=g.sample_fps, cancelled=cancelled,
                on_progress=lambda f: emit("analyze", 0.30 + 0.70 * f, "Analysing motion and colour"))
        else:
            notes.append("No video stream: audio-only analysis.")
        _save_signals(cache, audio, motion)
    emit("analyze", 1.0, "Signals ready")

    dur = max(duration_s, audio.duration_s)
    ch = C.build_channels(audio, motion, dur)
    params = C.DetectionParams(
        min_duration_s=settings.clips.min_duration_s, max_duration_s=settings.clips.max_duration_s,
        max_candidates=g.max_candidates, same_event_gap_s=g.same_event_gap_s)
    cands, sal = C.find_candidates(ch, dur, params)
    log.info("Found %d candidate moments from signals.", len(cands))
    if not cands:
        notes.append("No moments stood out from the video's own baseline. Try a longer or livelier video.")

    # ---- 2. speech --------------------------------------------------------
    if g.transcribe_scope == "full":
        windows = [(0.0, dur)]
    else:
        windows = speech.merge_windows([(c.start - 8, c.end + 8) for c in cands])
    emit("transcribe", 0.0, "Transcribing commentary")
    transcript = speech.transcribe_windows(
        wav, windows, settings.whisper, cancelled=cancelled,
        on_progress=lambda f: emit("transcribe", f, "Transcribing commentary"))
    emit("transcribe", 1.0, f"{len(transcript.words)} words transcribed")
    if not transcript.words:
        notes.append("No speech found in the analysed audio; clips will have no captions.")

    # Full-video transcripts can add commentary-only candidates the sensors missed.
    spch = speech.speech_channels(transcript, dur)
    if spch is not None and g.transcribe_scope == "full":
        ch["shout"] = np.maximum(ch["shout"], np.clip(spch["excite"] * 0.8, 0, 1)[: len(ch["shout"])]
                                 if len(spch["excite"]) >= len(ch["shout"]) else ch["shout"])
        cands, sal = C.find_candidates(ch, dur, params)

    # ---- 3. refine, score, describe --------------------------------------
    emit("highlights", 0.0, "Refining clip boundaries")
    if transcript.words:
        speech.refine_with_speech(cands, transcript, dur, params)
    scoring.score_candidates(cands, ch, sal, g.weights, min_duration_s=params.min_duration_s, speech=spch)

    if g.vision.enabled and video is not None:
        emit("highlights", 0.3, "Looking at keyframes with the vision model")
        notes.append(vision.run_vision(video, cands, g.vision, cancelled=cancelled,
                                       on_progress=lambda f: emit("highlights", 0.3 + 0.4 * f, "Vision")))
        scoring.score_candidates(cands, ch, sal, g.weights, min_duration_s=params.min_duration_s, speech=spch)
    if g.llm_refine and provider is not None:
        import asyncio

        emit("highlights", 0.75, "Refining titles with the language model")
        notes.append(asyncio.run(vision.llm_refine(cands, provider, detection_config)))
        scoring.score_candidates(cands, ch, sal, g.weights, min_duration_s=params.min_duration_s, speech=spch)

    for c in cands:
        metadata.build_metadata(c)
    cands = [c for c in cands if c.score >= g.min_score]
    cands.sort(key=lambda c: -c.score)
    # Final overlap guard: never two clips of the same moment.
    cands = _dedupe(cands, params)
    emit("highlights", 1.0, f"{len(cands)} highlights")

    (work_dir / "analysis.json").write_text(json.dumps(
        {"notes": notes, "candidates": [c.__dict__ for c in cands]}, default=str, indent=1), encoding="utf-8")
    return AnalysisResult(candidates=cands, transcript=transcript, notes=notes, duration_s=dur)


def _dedupe(cands: list[C.Candidate], params: C.DetectionParams) -> list[C.Candidate]:
    kept: list[C.Candidate] = []
    for c in cands:
        dup = False
        for k in kept:
            inter = min(c.end, k.end) - max(c.start, k.start)
            union = max(c.end, k.end) - min(c.start, k.start)
            if (inter / union > 0.6) or abs(c.peak - k.peak) < params.same_event_gap_s * 0.5:
                dup = True
                break
        if not dup:
            kept.append(c)
    return kept
