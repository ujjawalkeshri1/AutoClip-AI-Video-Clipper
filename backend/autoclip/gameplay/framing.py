"""Gameplay-aware vertical framing.

Face tracking is meaningless for gameplay, and a 9:16 crop of a 16:9 frame keeps
only ~32% of its width. So the choice is between:

* ``blur_fit``    whole frame, shown over a blurred copy of itself. Nothing is cut.
* ``motion_crop`` a 9:16 window that follows where the action is.
* ``center``      a plain centred crop.
* ``auto``        motion_crop only when it is safe: the HUD is not stranded outside
                  the window and the action stays in a narrow band; otherwise blur_fit.

"HUD" is estimated as pixels that stay put while the scene moves and that have
edges (icons, text, minimap). It is a heuristic and the preview lets you override.
"""

from __future__ import annotations

import logging
import subprocess
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from ..pipeline import ffmpeg
from ..pipeline.reframe.croppath import (
    CropKeyframe,
    CropPath,
    CropSegment,
    Strategy,
    centre_crop,
    decimate,
    target_crop_size,
)
from . import signals

log = logging.getLogger(__name__)

MODES = ("auto", "blur_fit", "motion_crop", "center")


@dataclass
class FramingDecision:
    mode: str
    reason: str
    hud_cut_ratio: float = 0.0
    action_spread: float = 0.0


def detect_hud(video: Path, start_s: float, end_s: float, *, samples: int = 24,
               width: int = 192) -> np.ndarray | None:
    """Boolean HUD mask (H x W, low-res) or None if it could not be estimated."""
    import cv2

    info = ffmpeg.probe(video)
    if not info.width or not info.height:
        return None
    height = max(2, int(round(width * info.height / info.width / 2) * 2))
    span = max(1.0, end_s - start_s)
    cmd = [ffmpeg.ffmpeg_path(), "-v", "error", "-nostdin", "-ss", f"{start_s:.2f}", "-t", f"{span:.2f}",
           "-i", str(video), "-an", "-vf", f"fps={samples / span:.4f},scale={width}:{height}",
           "-pix_fmt", "gray", "-f", "rawvideo", "-"]
    r = subprocess.run(cmd, capture_output=True, timeout=180,
                       creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    n = len(r.stdout) // (width * height)
    if n < 6:
        return None
    frames = np.frombuffer(r.stdout[: n * width * height], np.uint8).reshape(n, height, width).astype(np.float32)
    static = frames.std(axis=0) < 6.0
    mean = frames.mean(axis=0)
    gx = cv2.Sobel(mean, cv2.CV_32F, 1, 0)
    gy = cv2.Sobel(mean, cv2.CV_32F, 0, 1)
    edges = np.hypot(gx, gy) > 60.0
    # The scene must actually be moving, otherwise "static" means nothing.
    if float(frames.std(axis=0).mean()) < 4.0:
        return None
    mask = static & edges
    mask = cv2.dilate(mask.astype(np.uint8), np.ones((5, 5), np.uint8)).astype(bool)
    return mask if mask.sum() >= 12 else None


def hud_cut_ratio(mask: np.ndarray | None, x0_frac: float, x1_frac: float) -> float:
    """Fraction of HUD pixels that fall outside the horizontal window [x0, x1]."""
    if mask is None or mask.sum() == 0:
        return 0.0
    w = mask.shape[1]
    a, b = int(x0_frac * w), int(x1_frac * w)
    inside = mask[:, a:b].sum()
    return float(1.0 - inside / mask.sum())


def action_track(video: Path, start_s: float, end_s: float, source_duration: float, fps: float = 2.0):
    """Smoothed horizontal/vertical centre of motion over the clip (fractions 0..1)."""
    m = signals.analyze_motion(video, duration_s=source_duration, sample_fps=fps,
                               start_s=start_s, end_s=end_s)
    cx, cy, mot = m.channels["cx"], m.channels["cy"], m.channels["motion"]
    n = len(cx)
    w = np.clip(mot / (mot.max() + 1e-9), 0.05, 1.0)
    # Weighted smoothing: low-motion samples have unreliable centroids.
    k = 9
    num = np.convolve(cx * w, np.ones(k), "same")
    den = np.convolve(w, np.ones(k), "same") + 1e-9
    return (num / den).astype(np.float32), cy, mot, n


def choose_mode(requested: str, video: Path, start_s: float, end_s: float, src_w: int, src_h: int,
                ratio: str, source_duration: float) -> FramingDecision:
    if requested != "auto":
        return FramingDecision(requested, f"selected by you: {requested}")
    if ratio != "9:16" or src_w / src_h <= 9 / 16 + 0.05:
        return FramingDecision("center", "source is not wider than the target; nothing to fit")
    crop_w, _ = target_crop_size(src_w, src_h, 9, 16)
    window = crop_w / src_w
    mask = detect_hud(video, start_s, end_s)
    cx, _, _, _ = action_track(video, start_s, end_s, source_duration)
    spread = float(np.percentile(cx, 90) - np.percentile(cx, 10))
    centre = float(np.median(cx))
    x0 = min(max(0.0, centre - window / 2), 1 - window)
    cut = hud_cut_ratio(mask, x0, x0 + window)
    if cut > 0.35:
        return FramingDecision("blur_fit", f"HUD would be cut off ({cut:.0%} outside the crop)", cut, spread)
    if spread > window * 0.8:
        return FramingDecision("blur_fit", "action spreads wider than a vertical crop can hold", cut, spread)
    return FramingDecision("motion_crop", "action stays in a narrow band and HUD is mostly kept", cut, spread)


def build_gameplay_crop_path(video: Path, *, start_s: float, end_s: float, mode: str, ratio: str,
                             source_duration: float) -> tuple[CropPath, FramingDecision]:
    info = ffmpeg.probe(video)
    sw, sh = info.width or 1920, info.height or 1080
    dur = end_s - start_s
    aw, ah = {"9:16": (9, 16), "1:1": (1, 1), "16:9": (16, 9)}[ratio]
    decision = choose_mode(mode, video, start_s, end_s, sw, sh, ratio, source_duration)

    if decision.mode == "blur_fit" and ratio != "16:9":
        seg = CropSegment(start_s=0.0, end_s=dur, width=sw, height=sh, keyframes=[],
                          strategy=Strategy.WIDE, fit=True)
        return CropPath(source_width=sw, source_height=sh, segments=[seg]), decision
    if decision.mode in ("center", "blur_fit"):
        return centre_crop(sw, sh, dur, aspect_w=aw, aspect_h=ah), decision

    # motion_crop: pan the window along the smoothed action centre.
    cw, ch_ = target_crop_size(sw, sh, aw, ah)
    cx, cy, _, n = action_track(video, start_s, end_s, source_duration)
    dt = signals.STEP_S
    kfs: list[CropKeyframe] = []
    x = float(np.clip(cx[0] * sw - cw / 2, 0, sw - cw))
    max_speed = sw * 0.25 * dt  # px per step: no whip-pans
    dead = cw * 0.04
    for i in range(n):
        target = float(np.clip(cx[i] * sw - cw / 2, 0, sw - cw))
        if abs(target - x) > dead:
            x += float(np.clip(target - x, -max_speed, max_speed))
        y = float(np.clip(cy[i] * sh - ch_ / 2, 0, sh - ch_)) if ch_ < sh else 0.0
        kfs.append(CropKeyframe(t=round(i * dt, 3), x=x, y=y))
    seg = CropSegment(start_s=0.0, end_s=dur, width=cw, height=ch_, keyframes=decimate(kfs),
                      strategy=Strategy.TRACK)
    return CropPath(source_width=sw, source_height=sh, segments=[seg]), decision
