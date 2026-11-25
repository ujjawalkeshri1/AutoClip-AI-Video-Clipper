"""Speech handling for gameplay: windowed transcription, keywords, boundary snapping.

Gameplay is often silent or has sparse commentary, so unlike the talking-head
pipeline this never fails on "no speech": it returns an empty transcript and the
clip simply has no captions (optional hook text can be used instead).
"""

from __future__ import annotations

import logging
import re
from collections.abc import Callable
from pathlib import Path

import numpy as np

from ..config import WhisperSettings
from ..pipeline.transcript import Segment, Transcript, Word
from .candidates import LABELS, STEP_S, Candidate, DetectionParams
from .signals import grid_len

log = logging.getLogger(__name__)

KEYWORDS: dict[str, tuple[str, ...]] = {
    "POLICE_CHASE": ("cops", "cop ", "police", "wanted", "pull over", "chase", "star wanted"),
    "EXPLOSION": ("explod", "explosion", "boom", "blow up", "blew up", "grenade", "rpg"),
    "CRASH": ("crash", "wreck", "flipped", "rolled", "totaled"),
    "SHOOTOUT": ("shoot", "gun", "shot", "reload", "firefight"),
    "FUNNY": ("lol", "haha", "funny", "laughing", "i'm dead", "what the"),
    "NPC_INTERACTION": ("npc", "pedestrian", "this guy", "this lady"),
    "MISSION_HIGHLIGHT": ("mission", "objective", "heist", "robbery", "cutscene"),
}
EXCITED = ("oh my god", "no way", "what the", "holy", "bro", "dude", "wtf", "insane", "crazy", "lol", "haha")


def merge_windows(wins: list[tuple[float, float]], gap: float = 1.0) -> list[tuple[float, float]]:
    out: list[list[float]] = []
    for a, b in sorted(wins):
        if out and a <= out[-1][1] + gap:
            out[-1][1] = max(out[-1][1], b)
        else:
            out.append([a, b])
    return [(a, b) for a, b in out]


def transcribe_windows(
    wav: Path,
    windows: list[tuple[float, float]],
    settings: WhisperSettings,
    *,
    on_progress: Callable[[float], None] | None = None,
    cancelled: Callable[[], bool] | None = None,
) -> Transcript:
    """Transcribe only ``windows`` (absolute seconds); timestamps stay absolute.

    The model is loaded once. A window with no speech simply contributes nothing.
    """
    import soundfile as sf

    from ..cuda import ensure_cuda_libraries
    from ..pipeline.transcribe import _model_load_error, resolve_compute

    transcript = Transcript(language=settings.language, model=settings.model, source="whisper")
    if not windows:
        return transcript
    ensure_cuda_libraries()
    from faster_whisper import WhisperModel

    device, compute = resolve_compute(settings)
    log.info("Windowed transcription: model=%s device=%s compute=%s windows=%d",
             settings.model, device, compute, len(windows))
    try:
        model = WhisperModel(settings.model, device=device, compute_type=compute)
    except Exception as exc:
        raise _model_load_error(exc, device, compute) from exc

    info = sf.info(str(wav))
    sr = info.samplerate
    for i, (a, b) in enumerate(windows):
        if cancelled and cancelled():
            break
        a, b = max(0.0, a), min(info.frames / sr, b)
        if b - a < 1.0:
            continue
        with sf.SoundFile(str(wav)) as f:
            f.seek(int(a * sr))
            audio = f.read(int((b - a) * sr), dtype="float32")
        if audio.ndim > 1:
            audio = audio.mean(axis=1)
        segs, tinfo = model.transcribe(
            audio, language=settings.language or None, word_timestamps=True, vad_filter=True,
            vad_parameters={"min_silence_duration_ms": 500},
        )
        if not transcript.language:
            transcript.language = getattr(tinfo, "language", "") or ""
        for seg in segs:
            first = len(transcript.words)
            for w in getattr(seg, "words", None) or []:
                text = (w.word or "").strip()
                if text:
                    transcript.words.append(Word(text=text, start=a + float(w.start), end=a + float(w.end)))
            transcript.segments.append(Segment(
                text=(seg.text or "").strip(), start=a + float(seg.start), end=a + float(seg.end),
                first_word=first, last_word=max(first, len(transcript.words) - 1)))
        if on_progress:
            on_progress((i + 1) / len(windows))
    transcript.words.sort(key=lambda w: w.start)
    return transcript


def speech_channels(transcript: Transcript | None, duration_s: float) -> dict[str, np.ndarray] | None:
    """Speech rate and excitement on the shared timeline. None if there is no speech."""
    if transcript is None or not transcript.words:
        return None
    n = grid_len(duration_s)
    rate = np.zeros(n, dtype=np.float32)
    excite = np.zeros(n, dtype=np.float32)
    for w in transcript.words:
        i = min(n - 1, int(w.start / STEP_S))
        rate[i] += 1.0
        low = w.text.lower()
        if "!" in w.text or low.strip(".,?!") in ("what", "no", "oh", "wow", "bro", "dude", "holy"):
            excite[i] += 0.5
    k = np.ones(6) / 3.0  # words per second over ~3 s
    rate = np.convolve(rate, k, mode="same")
    base = max(1.0, float(np.median(rate[rate > 0])) if (rate > 0).any() else 1.0)
    excite = np.clip(np.convolve(excite, k, mode="same") + np.clip((rate / base - 1.3) * 0.5, 0, 1), 0, 1)
    return {"rate": rate.astype(np.float32), "excite": excite.astype(np.float32)}


def _text(transcript: Transcript, start: float, end: float) -> str:
    return " ".join(w.text for w in transcript.words if start <= w.start < end).strip()


def refine_with_speech(cands: list[Candidate], transcript: Transcript, duration_s: float,
                       params: DetectionParams) -> None:
    """Attach transcripts, use commentary to sharpen labels, snap edges to sentences."""
    for c in cands:
        _snap_to_sentences(c, transcript, duration_s, params)  # edges first, then read the final text
        c.transcript_text = _text(transcript, c.start, c.end)
        low = " " + c.transcript_text.lower() + " "
        hits = {cat: [k for k in kws if k in low] for cat, kws in KEYWORDS.items()}
        hits = {k: v for k, v in hits.items() if v}
        for cat in hits:
            if cat not in c.tags and cat != c.category:
                c.tags.append(cat)
        # Commentary can name an event the sensors can only call "action".
        if c.category in ("ACTION", "SURPRISE") and hits:
            best = max(hits, key=lambda k: len(hits[k]))
            c.category = best
            c.confidence = max(c.confidence, 0.6)
            c.evidence = [f"commentary mentions {', '.join(hits[best][:2]).strip()}"]
        elif c.category in hits:
            c.evidence.append("commentary agrees")
            c.confidence = min(1.0, c.confidence + 0.1)
        if any(x in low for x in EXCITED) and "STREAMER_REACTION" not in c.tags + [c.category]:
            c.tags.append("STREAMER_REACTION")


def _snap_to_sentences(c: Candidate, t: Transcript, duration_s: float, params: DetectionParams) -> None:
    words = t.words
    if not words:
        return
    # --- start: never begin mid-sentence
    i = next((k for k, w in enumerate(words) if w.end > c.start), None)
    if i is not None and words[i].start < c.start + 6 and i > 0:
        inside = words[i].start < c.start or (
            not words[i - 1].ends_sentence and words[i].start - words[i - 1].end < 0.7)
        if inside:
            j = i
            while j > 0 and not words[j - 1].ends_sentence and words[j].start - words[j - 1].end < 0.7 \
                    and c.start - words[j - 1].start < 6.0:
                j -= 1
            if words[j].start < c.peak - 1:
                new = max(0.0, words[j].start - 0.15)
                if new < c.start:
                    c.start = new
                    c.boundary_notes.append("start moved back to the beginning of a sentence")
    # --- end: finish the sentence, and do not cut right after the action
    k = max((n for n, w in enumerate(words) if w.start < c.end), default=None)
    if k is not None and k + 1 < len(words) and not words[k].ends_sentence:
        j = k
        while j + 1 < len(words) and not words[j].ends_sentence and words[j + 1].start - words[j].end < 0.7 \
                and words[j].end - c.end < 6.0:
            j += 1
        new = min(duration_s, words[j].end + 0.25)
        if new > c.end and new - c.start <= params.max_duration_s:
            c.end = new
            c.boundary_notes.append("end extended to finish the sentence")
    c.end = max(c.end, min(duration_s, c.peak + 2.0))
