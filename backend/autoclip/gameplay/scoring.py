"""Six-factor viral-potential scoring.

Each factor is computed independently on 0..1, then combined with configurable
weights (default 25/20/15/15/15/10). The result is a *heuristic estimate of
entertainment and engagement potential* derived from audio, motion, speech and
(optionally) vision evidence. It does not predict views: there is no audience
data in this system.
"""

from __future__ import annotations

import numpy as np

from .candidates import STEP_S, Candidate
from .signals import grid_len

DEFAULT_WEIGHTS = {
    "entertainment": 25.0,
    "unexpectedness": 20.0,
    "action": 15.0,
    "reaction": 15.0,
    "story": 15.0,
    "engagement": 10.0,
}
FACTOR_LABELS = {
    "entertainment": "Entertainment value",
    "unexpectedness": "Unexpectedness & surprise",
    "action": "Action & intensity",
    "reaction": "Streamer reaction",
    "story": "Storytelling & context",
    "engagement": "Short-form engagement potential",
}
SCORE_NOTE = (
    "Heuristic estimate of entertainment/engagement potential from audio, motion, speech and "
    "optional vision evidence. Not a prediction of views or audience behaviour."
)


def _clip01(x: float) -> float:
    return float(min(1.0, max(0.0, x)))


def _win(arr: np.ndarray, t0: float, t1: float) -> np.ndarray:
    a, b = max(0, int(t0 / STEP_S)), min(len(arr), max(int(t0 / STEP_S) + 1, int(t1 / STEP_S) + 1))
    return arr[a:b]


def score_candidates(
    cands: list[Candidate],
    ch: dict[str, np.ndarray],
    sal: np.ndarray,
    weights: dict[str, float] | None = None,
    *,
    min_duration_s: float = 20.0,
    speech: dict[str, np.ndarray] | None = None,
) -> None:
    """Fill ``factors`` (0..1), ``meta['points']`` and ``score`` (0..100) in place."""
    w = {**DEFAULT_WEIGHTS, **(weights or {})}
    total_w = sum(max(0.0, v) for v in w.values()) or 1.0
    peaks = sorted(c.salience for c in cands)

    for c in cands:
        pk = int(c.peak / STEP_S)
        near = lambda name, r=3.0: float(_win(ch[name], c.peak - r, c.peak + r).max(initial=0.0))  # noqa: E731
        lead = _win(sal, c.start, c.peak - 1.0)
        tail = _win(sal, c.peak + 1.0, c.end)
        clip_max = lambda name: float(_win(ch[name], c.start, c.end).max(initial=0.0))  # noqa: E731
        vision = c.meta.get("vision") or {}
        has_speech = speech is not None
        excite = float(_win(speech["excite"], c.start, c.end).max(initial=0.0)) if has_speech else 0.0

        # 1. Entertainment: how strongly it stands out, how many kinds of evidence agree.
        rank = (peaks.index(c.salience) + 1) / len(peaks) if peaks else 0.5
        groups = [
            max(near("motion"), near("burst"), near("stop")) > 0.4,
            max(near("flash"), near("warm"), near("lights")) > 0.4,
            max(near("impact"), near("gunfire"), near("siren")) > 0.4,
            max(near("shout"), near("laugh"), excite) > 0.4,
        ]
        diversity = sum(groups) / 4.0
        parts = [(0.45, 0.6 * c.salience + 0.4 * rank), (0.25, diversity),
                 (0.15, max(clip_max("shout"), clip_max("laugh"), excite))]
        interest = vision.get("interest", c.meta.get("llm_interest"))
        if interest is not None:
            parts.append((0.15, _clip01(float(interest) / 10.0)))
        f_ent = _clip01(sum(a * b for a, b in parts) / sum(a for a, _ in parts))

        # 2. Unexpectedness: contrast against the calm before, how fast it rose, how rare.
        contrast = _clip01((c.salience - c.baseline) / max(0.3, 1.0 - c.baseline))
        slope = _clip01((float(sal[pk]) - float(sal[max(0, pk - 3)])) / 0.5)
        rare = 1.0 - float((sal >= 0.8 * c.salience).mean())
        f_unex = _clip01(0.5 * contrast + 0.3 * slope + 0.2 * rare)
        if vision.get("unexpected"):
            f_unex = _clip01(0.85 * f_unex + 0.15)

        # 3. Action / intensity.
        f_act = _clip01(0.5 * near("motion") + 0.2 * max(near("impact"), near("gunfire"))
                        + 0.2 * max(near("flash"), near("warm"), near("lights")) + 0.1 * near("energy"))

        # 4. Streamer reaction (audio indicators, plus excitement of what was said).
        voice = max(clip_max("shout"), clip_max("laugh"))
        f_react = _clip01(0.6 * voice + 0.4 * excite if has_speech else voice)

        # 5. Story: a build-up, an event, a visible aftermath, sensible length.
        lead_len = c.peak - c.start
        tail_len = c.end - c.peak
        build = _clip01((c.salience - float(lead.mean())) / 0.9) if len(lead) and lead_len >= 4 else 0.3
        resolve = _clip01((c.salience - float(tail[-6:].mean())) / 0.9) if len(tail) and tail_len >= 3 else 0.3
        dur_fit = 1.0 if 25 <= c.duration <= 60 else 0.7 if c.duration >= min_duration_s else 0.4
        f_story = _clip01(0.35 * build + 0.35 * resolve + 0.2 * (1.0 if c.boundary_notes else 0.5) + 0.1 * dur_fit)
        if interest is not None:
            f_story = _clip01(0.85 * f_story + 0.15 * float(interest) / 10.0)

        # 6. Short-form engagement: a hook early on, pacing, visual variety.
        early = _win(sal, c.start, c.start + 3.0)
        hook = _clip01(float(early.mean()) / max(c.salience, 1e-6) * 1.2) if len(early) else 0.0
        variety = _clip01(float(_win(ch["motion"], c.start, c.end).std()) * 3.0)
        f_eng = _clip01(0.4 * max(hook, 0.25) + 0.25 * dur_fit + 0.2 * variety
                        + 0.15 * max(voice, excite, near("impact")))

        c.factors = {"entertainment": f_ent, "unexpectedness": f_unex, "action": f_act,
                     "reaction": f_react, "story": f_story, "engagement": f_eng}
        points = {k: round(c.factors[k] * w[k] / total_w * 100.0, 1) for k in c.factors}
        c.meta["points"] = points
        c.meta["max_points"] = {k: round(w[k] / total_w * 100.0, 1) for k in w}
        c.score = int(round(sum(points.values())))
        c.meta["score_note"] = SCORE_NOTE
