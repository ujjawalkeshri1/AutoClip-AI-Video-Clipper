"""Fuse signals into candidate highlight windows.

A candidate is found from *any* strong channel (noisy-OR fusion), so a quiet but
visually dramatic moment survives even when the streamer says nothing. Channel
scaling is relative to the video's own distribution with absolute floors, so a
loud video does not drown a quiet one and static footage does not get inflated.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

import numpy as np

from .signals import STEP_S, AudioSignals, MotionSignals, grid_len

log = logging.getLogger(__name__)

CATEGORIES = [
    "ACTION", "FUNNY", "SURPRISE", "POLICE_CHASE", "CRASH", "EXPLOSION",
    "SHOOTOUT", "NPC_INTERACTION", "STREAMER_REACTION", "MISSION_HIGHLIGHT",
]
LABELS = {c: c.replace("_", " ").title() for c in CATEGORIES}

#: (lead-up seconds, aftermath seconds) kept around each category's event.
PADS = {
    "EXPLOSION": (5, 5), "CRASH": (7, 5), "POLICE_CHASE": (8, 6), "SHOOTOUT": (6, 5),
    "STREAMER_REACTION": (6, 4), "FUNNY": (8, 5), "NPC_INTERACTION": (8, 6),
    "SURPRISE": (7, 5), "MISSION_HIGHLIGHT": (10, 8), "ACTION": (6, 5),
}

#: How much each normalised channel contributes to fused salience.
CHANNEL_WEIGHTS = {
    "motion": 0.55, "burst": 0.50, "flash": 0.80, "warm": 0.50, "lights": 0.60,
    "energy": 0.45, "impact": 0.85, "gunfire": 0.70, "siren": 0.60,
    "shout": 0.80, "laugh": 0.70, "cut": 0.25, "stop": 0.60,
}


@dataclass
class Candidate:
    start: float
    end: float
    peak: float
    category: str = "ACTION"
    confidence: float = 0.0
    tags: list[str] = field(default_factory=list)
    evidence: list[str] = field(default_factory=list)
    channel_peaks: dict[str, float] = field(default_factory=dict)
    salience: float = 0.0
    baseline: float = 0.0
    boundary_notes: list[str] = field(default_factory=list)
    factors: dict[str, float] = field(default_factory=dict)
    score: int = 0
    transcript_text: str = ""
    meta: dict = field(default_factory=dict)

    @property
    def duration(self) -> float:
        return self.end - self.start


@dataclass
class DetectionParams:
    min_duration_s: float = 20.0
    max_duration_s: float = 90.0
    max_candidates: int = 15
    same_event_gap_s: float = 15.0
    min_salience: float = 0.30


def _fit(arr: np.ndarray, n: int, fill: float = 0.0) -> np.ndarray:
    if len(arr) >= n:
        return arr[:n].astype(np.float32)
    return np.concatenate([arr, np.full(n - len(arr), fill, dtype=np.float32)]).astype(np.float32)


def soft(x: np.ndarray, floor: float, span: float, lo_pct: float = 70) -> np.ndarray:
    """Map ``x`` to 0..1 as contrast above the video's own baseline.

    The baseline is the ``lo_pct`` percentile (never below ``floor``), so
    sustained activity that is normal *for this video* scores low. The scale
    above it is a fixed ``span``: an anchor taken from a high percentile would
    let one huge event squash every moderate one.
    """
    if len(x) == 0:
        return x
    lo = max(float(np.percentile(x, lo_pct)), floor)
    return np.clip((x - lo) / span, 0, 1).astype(np.float32)


def smooth(x: np.ndarray, width: int) -> np.ndarray:
    if width <= 1:
        return x
    return np.convolve(x, np.ones(width) / width, mode="same").astype(np.float32)


def build_channels(audio: AudioSignals | None, motion: MotionSignals | None,
                   duration_s: float) -> dict[str, np.ndarray]:
    """Normalised 0..1 channels on the shared timeline."""
    n = grid_len(duration_s)
    z = np.zeros(n, dtype=np.float32)
    ch: dict[str, np.ndarray] = {k: z.copy() for k in CHANNEL_WEIGHTS}
    ch["cx"] = np.full(n, 0.5, dtype=np.float32)
    ch["cy"] = np.full(n, 0.5, dtype=np.float32)
    ch["motion_raw"] = z.copy()

    if motion is not None:
        m = {k: _fit(v, n, 0.5 if k in ("cx", "cy") else 0.0) for k, v in motion.channels.items()}
        raw = smooth(m["motion"], 3)
        diff = smooth(m["diff"], 3)
        # Object motion (flow residual) or raw frame change, whichever is stronger.
        ch["motion"] = np.maximum(soft(raw, 0.004, 0.012), 0.9 * soft(diff, 0.02, 0.05))
        raw = np.maximum(raw, diff * 0.3)
        ch["motion_raw"] = raw
        back = np.array([raw[max(0, i - 8): max(1, i)].mean() for i in range(n)], dtype=np.float32)
        ch["burst"] = soft(np.maximum(0, raw - back), 0.003, 0.010)
        mv = soft(diff, 0.02, 0.05)
        stop = np.zeros(n, dtype=np.float32)
        for i in range(8, n - 4):
            before, after = float(mv[i - 8:i].mean()), float(mv[i + 1:i + 5].mean())
            if before >= 0.3:
                stop[i] = float(np.clip(((before - after) / before - 0.4) / 0.5, 0, 1))
        ch["stop"] = smooth(stop, 3)
        ch["flash"] = soft(m["flash"], 0.02, 0.08)
        ch["warm"] = soft(m["warm"], 0.02, 0.15)
        ch["lights"] = soft(m["lights"], 0.20, 0.50)
        ch["cut"] = np.clip((m["cut"] - 0.35) / 0.4, 0, 1)
        ch["cx"], ch["cy"] = m["cx"], m["cy"]

    if audio is not None:
        a = {k: _fit(v, n, -90.0 if k == "rms_db" else 0.0) for k, v in audio.channels.items()}
        ch["energy"] = soft(a["rms_db"], -40.0, 15.0)
        ch["impact"] = soft(a["low_onset"], 1.5, 3.0)
        ch["gunfire"] = smooth(np.clip((a["impulse"] - 0.5) / 2.5, 0, 1), 3)
        ch["siren"] = soft(a["siren"], 0.15, 0.15)
        ch["shout"] = a["shout"]
        ch["laugh"] = a["laugh"]
    return ch


def salience_curve(ch: dict[str, np.ndarray]) -> np.ndarray:
    """Noisy-OR over channels: one strong signal is enough."""
    keep = np.ones_like(ch["motion"])
    for name, w in CHANNEL_WEIGHTS.items():
        keep *= 1.0 - w * ch[name]
    return smooth((1.0 - keep).astype(np.float32), 3)


# --------------------------------------------------------------------------
# Peak picking and windows
# --------------------------------------------------------------------------


def find_candidates(ch: dict[str, np.ndarray], duration_s: float,
                    params: DetectionParams) -> tuple[list[Candidate], np.ndarray]:
    from scipy.signal import find_peaks

    sal = salience_curve(ch)
    if len(sal) < 4:
        return [], sal
    height = max(params.min_salience, float(np.percentile(sal, 85)))
    peaks, props = find_peaks(sal, height=height, prominence=0.10, distance=int(8 / STEP_S))
    order = np.argsort(-(props["prominences"] * sal[peaks]))
    cands: list[Candidate] = []
    for k in order[: params.max_candidates * 3]:
        pk = int(peaks[k])
        c = Candidate(start=0, end=0, peak=pk * STEP_S, salience=float(sal[pk]))
        categorize(c, ch, sal)
        lead, tail = PADS.get(c.category, PADS["ACTION"])
        left, right = _extent(sal, pk)
        c.start = max(0.0, min(left * STEP_S, c.peak - 2.0) - lead)
        c.end = min(duration_s, max(right * STEP_S, c.peak + 2.0) + tail)
        _apply_duration(c, duration_s, params)
        lo = max(0, int((c.peak - 20) / STEP_S))
        hi = max(lo + 1, int((c.peak - 4) / STEP_S))
        c.baseline = float(np.median(sal[lo:hi])) if hi > lo else 0.0
        cands.append(c)

    cands = merge_candidates(cands, sal, params)
    for c in cands:
        snap_to_calm(c, ch, sal, duration_s)
        _apply_duration(c, duration_s, params)  # snapping/splitting can shorten a clip
        c.boundary_notes.append(f"{c.duration:.0f}s window: lead-up, event, aftermath")
    cands.sort(key=lambda c: -c.salience)
    return cands[: params.max_candidates], sal


def _extent(sal: np.ndarray, pk: int, frac: float = 0.4, max_run_s: float = 30.0) -> tuple[int, int]:
    level = max(0.15, float(sal[pk]) * frac)
    limit = int(max_run_s / STEP_S)
    left = pk
    while left > 0 and sal[left - 1] >= level and pk - left < limit:
        left -= 1
    right = pk
    while right < len(sal) - 1 and sal[right + 1] >= level and right - pk < limit:
        right += 1
    return left, right


def _apply_duration(c: Candidate, duration_s: float, params: DetectionParams) -> None:
    if c.duration < params.min_duration_s:
        deficit = params.min_duration_s - c.duration
        # More context before than after: the setup is what makes a payoff land.
        c.start = max(0.0, c.start - deficit * 0.55)
        c.end = min(duration_s, c.end + deficit * 0.45)
        if c.duration < params.min_duration_s:  # hit a video edge
            if c.start <= 0:
                c.end = min(duration_s, c.start + params.min_duration_s)
            else:
                c.start = max(0.0, c.end - params.min_duration_s)
    if c.duration > params.max_duration_s:
        lead, tail = PADS.get(c.category, PADS["ACTION"])
        c.start = max(c.start, c.peak - max(lead, params.max_duration_s * 0.4))
        c.end = min(c.end, c.start + params.max_duration_s)


def merge_candidates(cands: list[Candidate], sal: np.ndarray, params: DetectionParams) -> list[Candidate]:
    """Merge one event seen twice; split distinct nearby moments at the calm between them."""
    cands = sorted(cands, key=lambda c: c.peak)
    out: list[Candidate] = []
    for c in cands:
        if not out:
            out.append(c)
            continue
        p = out[-1]
        same = c.peak - p.peak < params.same_event_gap_s
        overlap = min(p.end, c.end) - max(p.start, c.start)
        # Only one event seen twice is merged. Distinct moments that merely have
        # overlapping context windows are kept apart and split at the calm between.
        if same:
            keep, other = (p, c) if p.salience >= c.salience else (c, p)
            generic = ("ACTION", "SURPRISE")
            # The event that happened matters more than the louder run-up to it.
            if other.category not in generic and (
                keep.category in generic or other.confidence > keep.confidence
            ):
                keep.category, keep.confidence = other.category, other.confidence
                keep.evidence, keep.peak = list(other.evidence), other.peak
            keep.start = min(p.start, c.start)
            keep.end = max(p.end, c.end)
            keep.salience = max(p.salience, c.salience)
            keep.tags = sorted((set(keep.tags) | set(other.tags) | {other.category}) - {keep.category, "ACTION"})
            out[-1] = keep
        elif overlap > 0:
            i0, i1 = int(p.peak / STEP_S), int(c.peak / STEP_S)
            valley = (i0 + int(np.argmin(sal[i0:i1 + 1]))) * STEP_S
            p.end, c.start = valley, valley
            p.boundary_notes.append("split from a neighbouring moment at the calm between them")
            out.append(c)
        else:
            out.append(c)
    for c in out:  # a merged window can exceed the cap
        if c.duration > params.max_duration_s:
            c.start = max(c.start, c.peak - params.max_duration_s * 0.4)
            c.end = min(c.end, c.start + params.max_duration_s)
    return out


def snap_to_calm(c: Candidate, ch: dict[str, np.ndarray], sal: np.ndarray, duration_s: float) -> None:
    """Visual-event boundaries for clips with no speech to snap to."""
    def argmin_in(lo: float, hi: float, prefer_late: bool) -> float:
        a, b = max(0, int(lo / STEP_S)), min(len(sal) - 1, int(hi / STEP_S))
        if b <= a:
            return lo
        seg = sal[a:b + 1]
        m = seg.min()
        idx = np.flatnonzero(seg <= m + 0.02)
        return (a + int(idx[-1] if prefer_late else idx[0])) * STEP_S

    start = argmin_in(c.start - 3, min(c.start + 3, c.peak - 1.0), prefer_late=False)
    end = argmin_in(max(c.end - 3, c.peak + 2.0), c.end + 4, prefer_late=False)
    cut = ch["cut"]
    for t, edge in ((start, "start"), (end, "end")):
        a, b = max(0, int((t - 2) / STEP_S)), min(len(cut) - 1, int((t + 2) / STEP_S))
        hits = np.flatnonzero(cut[a:b + 1] > 0.6)
        if len(hits):
            tc = (a + int(hits[0])) * STEP_S
            if edge == "start" and tc < c.peak - 1:
                start = tc
            if edge == "end" and tc > c.peak + 2:
                end = tc
    c.start, c.end = max(0.0, start), min(duration_s, end)
    c.boundary_notes.append("edges placed at calm points / scene cuts")


# --------------------------------------------------------------------------
# Categorisation
# --------------------------------------------------------------------------


def categorize(c: Candidate, ch: dict[str, np.ndarray], sal: np.ndarray) -> None:
    pk = int(c.peak / STEP_S)
    a, b = max(0, pk - 6), min(len(sal), pk + 7)  # +-3 s

    def peak(name: str) -> float:
        return float(ch[name][a:b].max()) if b > a else 0.0

    v = {k: peak(k) for k in CHANNEL_WEIGHTS}
    c.channel_peaks = {k: round(x, 3) for k, x in v.items() if x > 0.05}

    # Gunfire clicks carry low-frequency energy too; they are not a crash/boom.
    v["impact"] = v["impact"] * (1.0 - 0.8 * v["gunfire"])
    scores: dict[str, tuple[float, list[str]]] = {}
    vis_flash = max(v["flash"], v["warm"])
    if vis_flash > 0.35 and (v["impact"] > 0.35 or vis_flash > 0.75):
        scores["EXPLOSION"] = (0.5 * vis_flash + 0.5 * v["impact"] + 0.1,
                               ["bright flash with fiery colours", "low-frequency audio impact"]
                               if v["impact"] > 0.35 else ["bright flash with fiery colours (no audible boom)"])
    if (v["stop"] > 0.4 and vis_flash < 0.5) or (v["burst"] > 0.6 and v["impact"] > 0.4 and vis_flash < 0.3):
        stopped = v["stop"] > 0.4
        scores["CRASH"] = (0.5 * max(v["stop"], v["burst"]) + 0.25 * max(v["impact"], 0.3) + 0.15,
                           ["fast movement that stops abruptly" if stopped else "sudden violent motion burst"]
                           + (["impact sound"] if v["impact"] > 0.35 else
                              ["no loud impact sound (could also be braking or a pause)"]))
    if (v["lights"] > 0.4 or v["siren"] > 0.4) and v["motion"] > 0.25:
        scores["POLICE_CHASE"] = (0.6 * max(v["lights"], v["siren"]) + 0.3 * v["motion"] + 0.1,
                                  [e for e, ok in (("alternating red/blue lights", v["lights"] > 0.4),
                                                   ("siren-like tonal sweep", v["siren"] > 0.4)) if ok]
                                  + ["sustained fast motion"])
    if v["gunfire"] > 0.45:
        scores["SHOOTOUT"] = (0.7 * v["gunfire"] + 0.2 * max(v["flash"], v["motion"]) + 0.1,
                              ["rapid sharp audio impulses (gunfire-like)"])
    voice = max(v["shout"], v["laugh"])
    if voice > 0.45:
        scores["STREAMER_REACTION"] = (0.8 * voice + 0.1, [
            "loud, bright voice-band audio (shout-like)" if v["shout"] >= v["laugh"]
            else "bursty voice-band audio (laughter-like)"])
        if v["laugh"] > 0.5 and v["motion"] < 0.4:
            scores["FUNNY"] = (0.7 * v["laugh"] + 0.1, ["laughter-like audio during low action"])
    base = float(np.median(sal[max(0, pk - 40): max(1, pk - 8)])) if pk > 10 else 0.0
    contrast = max(0.0, float(sal[pk]) - base)
    if contrast > 0.45 and float(sal[pk]) > 0.55:
        scores["SURPRISE"] = (0.45 + 0.4 * contrast, [f"sudden jump from a calm stretch (+{contrast:.2f})"])
    scores["ACTION"] = (0.25 + 0.4 * max(v["motion"], v["energy"]), ["high motion/intensity"]
                        if v["motion"] > 0.3 else ["elevated audio intensity"])

    # A confident specific detection beats the generic labels, which describe
    # *how* a moment stood out rather than *what* it was.
    specific = {k: sc for k, sc in scores.items()
                if k not in ("SURPRISE", "ACTION") and sc[0] >= 0.5}
    pool = specific or {k: sc for k, sc in scores.items() if k != "ACTION"} or scores
    best = max(pool, key=lambda k: pool[k][0])
    if not specific and best != "SURPRISE" and "SURPRISE" in scores:
        best = "SURPRISE" if scores["SURPRISE"][0] >= pool[best][0] else best
    c.category = best
    c.confidence = float(min(1.0, scores[best][0]))
    c.evidence = list(scores[best][1])
    c.tags = sorted(k for k, sc in scores.items()
                    if k != best and k != "ACTION" and sc[0] >= 0.5)
