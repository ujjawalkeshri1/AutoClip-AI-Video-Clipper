"""Cheap per-timestep signals from audio and video.

Both extractors stream: audio is read in blocks and video frames are piped from
ffmpeg at low resolution, so memory stays flat regardless of video length.
Every output array lives on one shared timeline of ``STEP_S`` seconds.
"""

from __future__ import annotations

import logging
import subprocess
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

from ..pipeline import ffmpeg

log = logging.getLogger(__name__)

#: Resolution of the shared timeline, in seconds.
STEP_S = 0.5


class SignalError(RuntimeError):
    """Signal extraction failed."""


@dataclass
class AudioSignals:
    duration_s: float
    #: name -> array on the STEP_S grid. Keys: rms_db, low_onset, onset,
    #: impulse, speech_ratio, centroid, siren, laugh, shout.
    channels: dict[str, np.ndarray] = field(default_factory=dict)


@dataclass
class MotionSignals:
    duration_s: float
    #: name -> array on the STEP_S grid. Keys: motion, global_motion, flash,
    #: warm, red, blue, lights, cut, cx, cy, luma.
    channels: dict[str, np.ndarray] = field(default_factory=dict)
    sample_fps: float = 1.0


def grid_len(duration_s: float) -> int:
    return max(1, int(np.ceil(duration_s / STEP_S)))


# --------------------------------------------------------------------------
# Audio
# --------------------------------------------------------------------------


def analyze_audio(
    wav: Path,
    *,
    on_progress: Callable[[float], None] | None = None,
    cancelled: Callable[[], bool] | None = None,
    block_s: float = 60.0,
) -> AudioSignals:
    """Extract audio signals from a mono WAV, a block at a time."""
    import librosa
    import soundfile as sf

    info = sf.info(str(wav))
    sr = info.samplerate
    duration = info.frames / sr
    n = grid_len(duration)
    names = ["rms_db", "low_onset", "onset", "impulse", "speech_ratio", "centroid",
             "siren", "laugh", "shout"]
    out = {k: np.zeros(n, dtype=np.float32) for k in names}
    out["rms_db"][:] = -90.0

    n_fft, hop = 1024, 256
    freqs = np.fft.rfftfreq(n_fft, 1.0 / sr)
    low = (freqs >= 20) & (freqs <= 160)
    speech = (freqs >= 300) & (freqs <= 3400)
    siren_band = (freqs >= 500) & (freqs <= 1800)
    frames_per_step = int(round(STEP_S * sr / hop))
    block = int(block_s * sr)
    # Block length is a multiple of the grid so block edges land on steps.
    block -= block % int(STEP_S * sr)

    done = 0
    with sf.SoundFile(str(wav)) as f:
        step_index = 0
        while True:
            if cancelled and cancelled():
                raise SignalError("Cancelled.")
            data = f.read(block, dtype="float32", always_2d=False)
            if len(data) == 0:
                break
            if data.ndim > 1:
                data = data.mean(axis=1)
            _audio_block(data, sr, n_fft, hop, low, speech, siren_band, freqs,
                         frames_per_step, step_index, out, librosa)
            step_index += int(np.ceil(len(data) / (STEP_S * sr)))
            done += len(data)
            if on_progress:
                on_progress(min(1.0, done / max(1, info.frames)))

    # Laughter / shouting need context across steps, so derive them last.
    _derive_reaction(out)
    return AudioSignals(duration_s=duration, channels=out)


def _audio_block(data, sr, n_fft, hop, low, speech, siren_band, freqs, fps_step,
                 step0, out, librosa) -> None:
    if len(data) < n_fft:
        return
    stft = np.abs(librosa.stft(data, n_fft=n_fft, hop_length=hop, center=False)) ** 2
    total = stft.sum(axis=0) + 1e-12
    rms = np.sqrt(np.mean(librosa.util.frame(data, frame_length=n_fft, hop_length=hop) ** 2,
                          axis=0))[: stft.shape[1]]
    db = 20 * np.log10(rms + 1e-6)
    low_e = np.log1p(stft[low].sum(axis=0) * 1e3)
    low_on = np.maximum(0, np.diff(low_e, prepend=low_e[0]))
    flux = np.maximum(0, np.diff(np.log1p(stft * 1e3), axis=1, prepend=0)).sum(axis=0)
    speech_ratio = stft[speech].sum(axis=0) / total
    centroid = (freqs[:, None] * stft).sum(axis=0) / total
    # Dominant frequency in the siren band, and how tonal it is.
    band = stft[siren_band]
    dom = freqs[siren_band][band.argmax(axis=0)]
    tonal = band.max(axis=0) / (band.sum(axis=0) + 1e-12)
    # Gunfire-ish: sharp broadband flux spikes.
    # Spikes must stand well above the block's typical flux (median-based, so a
    # calm block yields none instead of always flagging its top decile).
    thresh = 8.0 * np.median(flux) + 40.0
    spikes = (flux > thresh) & (flux >= np.roll(flux, 1)) & (flux >= np.roll(flux, -1))

    nsteps = int(np.ceil(stft.shape[1] / fps_step))
    for i in range(nsteps):
        a, b = i * fps_step, min(stft.shape[1], (i + 1) * fps_step)
        idx = step0 + i
        if idx >= len(out["rms_db"]) or b <= a:
            continue
        out["rms_db"][idx] = db[a:b].max()
        out["low_onset"][idx] = low_on[a:b].max()
        out["onset"][idx] = flux[a:b].max()
        out["impulse"][idx] = spikes[a:b].sum()
        out["speech_ratio"][idx] = np.average(speech_ratio[a:b], weights=total[a:b])
        out["centroid"][idx] = centroid[a:b].mean()
        # Siren: tonal, with a frequency that sweeps smoothly over ~1-2 s.
        out["siren"][idx] = tonal[a:b].mean() * min(1.0, np.std(dom[a:b]) / 150.0 + 0.0)
    # Sweep needs a longer window than one step: refine below in _derive_reaction.


def _derive_reaction(out: dict[str, np.ndarray]) -> None:
    rms = out["rms_db"]
    n = len(rms)
    if n < 6:
        return
    # Siren: smooth over 3 s; keep only where tonal energy persists.
    k = np.ones(6) / 6
    out["siren"] = np.convolve(out["siren"], k, mode="same").astype(np.float32)

    baseline = np.median(rms)
    spread = 1.4826 * np.median(np.abs(rms - baseline)) + 1.0
    z = (rms - baseline) / spread
    voice = out["speech_ratio"] > 0.55
    bright = (out["centroid"] > 900) & (out["centroid"] < 3500)
    # Shouting indicator: loud relative to the video's baseline, voice-band
    # dominated, with a bright spectrum. A heuristic, not a classifier.
    # A siren is loud, voice-band and bright too, but far more tonal: suppress.
    not_siren = 1.0 - np.clip(out["siren"] / 0.25, 0, 1)
    out["shout"] = (np.clip((z - 1.0) / 3.0, 0, 1) * voice * bright * not_siren).astype(np.float32)

    # Laughter indicator: voice-band energy whose envelope pulses at 3-8 Hz.
    # At STEP_S=0.5 that modulation is invisible, so use the onset channel's
    # variability as a proxy: bursty voice-band energy with frequent small peaks.
    env = np.maximum(out["onset"], 0)
    win = 6
    var = np.array([env[max(0, i - win): i + win].std() / (env[max(0, i - win): i + win].mean() + 1e-6)
                    for i in range(n)])
    out["laugh"] = (np.clip((var - 0.6) / 1.2, 0, 1) * voice
                    * np.clip((z + 0.5) / 2.0, 0, 1)).astype(np.float32)


# --------------------------------------------------------------------------
# Video
# --------------------------------------------------------------------------

FLOW_BASE_FPS = 4  # decode rate; flow runs on every ``4/sample_fps``-th pair


def analyze_motion(
    video: Path,
    *,
    duration_s: float,
    sample_fps: float = 1.0,
    width: int = 160,
    start_s: float = 0.0,
    end_s: float | None = None,
    on_progress: Callable[[float], None] | None = None,
    cancelled: Callable[[], bool] | None = None,
) -> MotionSignals:
    """Stream low-resolution frames from ffmpeg and compute motion signals.

    Optical flow (Farneback) is computed on a pair of frames 0.25 s apart,
    roughly ``sample_fps`` times per second. Cheap per-frame statistics
    (luminance, colour) are taken on every decoded frame so a one-frame flash
    is not missed at 1 FPS sampling.
    """
    import cv2

    info = ffmpeg.probe(video)
    if not info.has_video or not info.width or not info.height:
        raise SignalError(f"{video.name} has no video stream.")
    height = max(2, int(round(width * info.height / info.width / 2) * 2))
    end = end_s if end_s is not None else duration_s
    span = max(0.1, end - start_s)

    cmd = [ffmpeg.ffmpeg_path(), "-v", "error", "-nostdin"]
    if start_s > 0:
        cmd += ["-ss", f"{start_s:.3f}"]
    cmd += ["-t", f"{span:.3f}", "-i", str(video), "-an", "-sn",
            "-vf", f"fps={FLOW_BASE_FPS},scale={width}:{height}:flags=area",
            "-pix_fmt", "bgr24", "-f", "rawvideo", "-"]
    flow_stride = max(1, int(round(FLOW_BASE_FPS / max(sample_fps, 0.25))))
    frame_bytes = width * height * 3

    n = grid_len(span)
    keys = ["motion", "diff", "global_motion", "flash", "warm", "red", "blue", "lights",
            "cut", "cx", "cy", "luma"]
    out = {k: np.zeros(n, dtype=np.float32) for k in keys}
    out["cx"][:] = 0.5
    out["cy"][:] = 0.5

    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, bufsize=frame_bytes * 8,
                            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    assert proc.stdout is not None
    prev_gray = prev_luma = prev_hist = prev_gn = None
    prev_flow_gray = None
    idx = 0
    dt = 1.0 / FLOW_BASE_FPS
    yy, xx = np.mgrid[0:height, 0:width]
    xx = xx / width
    yy = yy / height
    try:
        while True:
            if cancelled and cancelled():
                raise SignalError("Cancelled.")
            raw = proc.stdout.read(frame_bytes)
            if len(raw) < frame_bytes:
                break
            frame = np.frombuffer(raw, dtype=np.uint8).reshape(height, width, 3)
            t = idx * dt
            step = min(n - 1, int(t / STEP_S))
            gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
            luma = float(gray.mean())
            b, g, r = (frame[..., 0].astype(np.int16), frame[..., 1].astype(np.int16),
                       frame[..., 2].astype(np.int16))
            warm = float(((r > 200) & (g > 70) & (g < 210) & (b < 130) & (r > g + 30)).mean())
            red = float(((r > 140) & (r - np.maximum(g, b) > 70)).mean())
            blue = float(((b > 140) & (b - np.maximum(r, g) > 70)).mean())
            hist = np.concatenate([np.bincount((frame[..., c] >> 4).ravel(), minlength=16)
                                   for c in range(3)]).astype(np.float32)
            hist /= hist.sum()

            out["luma"][step] = max(out["luma"][step], luma)
            out["warm"][step] = max(out["warm"][step], warm)
            out["red"][step] = max(out["red"][step], red)
            out["blue"][step] = max(out["blue"][step], blue)
            gn = gray.astype(np.float32) - luma
            if prev_gn is not None:
                # Frame difference needs no flow convergence, so it still
                # registers fast scenes where optical flow breaks down. Mean
                # brightness is removed first so flashes and flicker (handled
                # by the flash channel) do not read as movement.
                d = float(np.abs(gn - prev_gn).mean() / 255.0)
                out["diff"][step] = max(out["diff"][step], d)
            if prev_luma is not None:
                out["flash"][step] = max(out["flash"][step], max(0.0, luma - prev_luma) / 255.0)
                cut = float(np.abs(hist - prev_hist).sum() / 6.0)
                out["cut"][step] = max(out["cut"][step], cut)
            if prev_gray is not None and idx % flow_stride == 0:
                flow = cv2.calcOpticalFlowFarneback(prev_gray, gray, None, 0.5, 3, 15, 3, 5, 1.2, 0)
                gmed = np.median(flow.reshape(-1, 2), axis=0)
                resid = np.linalg.norm(flow - gmed, axis=2) / width
                out["motion"][step] = max(out["motion"][step], float(resid.mean()))
                out["global_motion"][step] = max(out["global_motion"][step],
                                                 float(np.linalg.norm(gmed) / width))
                wsum = resid.sum()
                if wsum > 1e-6:
                    out["cx"][step] = float((resid * xx).sum() / wsum)
                    out["cy"][step] = float((resid * yy).sum() / wsum)
            prev_gray, prev_luma, prev_hist, prev_gn = gray, luma, hist, gn
            idx += 1
            if on_progress and idx % 40 == 0:
                on_progress(min(1.0, t / span))
    finally:
        proc.stdout.close()
        if proc.poll() is None:
            proc.kill()
        proc.wait()

    if idx == 0:
        err = (proc.stderr.read().decode(errors="replace") if proc.stderr else "")[-400:]
        raise SignalError(f"ffmpeg produced no frames for {video.name}. {err}")

    # Police lights alternate red and blue; require both to be present nearby.
    red, blue = out["red"], out["blue"]
    k = np.ones(5)
    rr = np.convolve(red, k, mode="same")
    bb = np.convolve(blue, k, mode="same")
    out["lights"] = np.minimum(rr, bb).astype(np.float32)

    if on_progress:
        on_progress(1.0)
    return MotionSignals(duration_s=span, channels=out, sample_fps=sample_fps)
