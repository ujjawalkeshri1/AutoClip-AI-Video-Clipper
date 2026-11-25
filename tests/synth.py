"""Synthetic gameplay-like test footage with known events.

This is NOT GTA footage. It exists to prove the signal pipeline can find
*known* events, including a silent visual-only one. It says nothing about
accuracy on real game footage.

Ground truth (seconds), for a 120 s clip:
    20-24  gunfire burst (audio impulses, small muzzle flicker)
    40     explosion: white-orange flash + low-frequency boom
    70-78  police chase: fast scroll, red/blue lights, siren sweep
    90-95  fast driving, then at 95 a SILENT crash (abrupt stop, shake, no boom)
    105    streamer-style shout (voiced, loud, bright)
Everything else is calm slow driving with quiet ambience.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import numpy as np

EVENTS = {
    "gunfire": (20.0, 24.0),
    "explosion": (40.0, 42.0),
    "chase": (70.0, 78.0),
    "crash": (95.0, 97.0),
    "shout": (105.0, 107.0),
}

W, H, FPS, SR = 640, 360, 20, 16000


def _speed(t: float) -> float:
    if 70 <= t < 78:
        return 28.0
    if 90 <= t < 95:
        return 32.0
    if 95 <= t < 95.4:
        return 0.0
    return 2.0


def _audio(duration: float) -> np.ndarray:
    import scipy.signal as sig

    rng = np.random.default_rng(7)
    n = int(duration * SR)
    t = np.arange(n) / SR
    a = rng.normal(0, 0.004, n) + 0.004 * np.sin(2 * np.pi * 95 * t)  # ambience + hum
    # explosion boom: low-passed noise burst, fast attack, slow decay
    s = int(40.0 * SR)
    m = int(1.6 * SR)
    boom = sig.lfilter(*sig.butter(2, 140 / (SR / 2)), rng.normal(0, 1, m))
    if s + m <= n:  # events past the requested duration are simply omitted
        a[s:s + m] += 0.9 * boom / np.abs(boom).max() * np.exp(-np.arange(m) / (0.5 * SR))
    # gunfire: sharp broadband clicks
    for ts in np.arange(20.0, min(24.0, duration - 0.1), 0.17):
        i = int(ts * SR)
        a[i:i + 320] += 0.5 * rng.normal(0, 1, 320) * np.exp(-np.arange(320) / 70)
    # siren sweep 700-1300 Hz at 0.5 Hz
    s0, s1 = int(70 * SR), int(78 * SR)
    if s1 <= n:
        ts = t[s0:s1] - 70
        phase = 2 * np.pi * (1000 * ts - (300 / (2 * np.pi * 0.5)) * np.cos(2 * np.pi * 0.5 * ts))
        a[s0:s1] += 0.18 * np.sin(phase)
    # silent crash: tiny soft thump only (well under the explosion's level)
    i = int(95.0 * SR)
    th = sig.lfilter(*sig.butter(2, 300 / (SR / 2)), rng.normal(0, 1, int(0.25 * SR)))
    if i + len(th) <= n:
        a[i:i + len(th)] += 0.06 * th / np.abs(th).max()
    # shout: voiced harmonic stack with formant-ish emphasis around 1.2-2 kHz
    s0, s1 = int(105 * SR), int(107 * SR)
    if s1 <= n:
        ts = t[s0:s1]
        f0 = 240 + 30 * np.sin(2 * np.pi * 3 * ts)
        voice = sum(np.sin(2 * np.pi * np.cumsum(f0 * k) / SR) * np.exp(-((f0 * k - 1500) / 1200) ** 2)
                    for k in range(1, 14))
        a[s0:s1] += 0.5 * voice / np.abs(voice).max() * np.hanning(s1 - s0) ** 0.3
    return np.clip(a, -1, 1).astype(np.float32)


def make_gameplay_video(path: Path, duration: float = 120.0) -> Path:
    import cv2
    import soundfile as sf

    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    wav = path.with_suffix(".wav")
    sf.write(str(wav), _audio(duration), SR)

    rng = np.random.default_rng(3)
    tile = rng.integers(40, 200, (H, W * 3, 3), dtype=np.uint8)
    tile = cv2.GaussianBlur(tile, (0, 0), 9)
    tile = cv2.normalize(tile, None, 30, 200, cv2.NORM_MINMAX)

    hud = np.zeros((H, W, 3), np.uint8)
    cv2.circle(hud, (70, H - 70), 50, (255, 255, 255), 3)
    cv2.rectangle(hud, (W - 190, H - 40), (W - 20, H - 20), (255, 255, 255), 3)
    cv2.putText(hud, "HP", (W - 235, H - 22), cv2.FONT_HERSHEY_SIMPLEX, 0.9, (255, 255, 255), 2)
    hud_mask = hud.sum(axis=2) > 0

    cmd = ["ffmpeg", "-y", "-v", "error", "-f", "rawvideo", "-pix_fmt", "bgr24", "-s", f"{W}x{H}",
           "-r", str(FPS), "-i", "-", "-i", str(wav), "-c:v", "libx264", "-preset", "ultrafast",
           "-crf", "24", "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "96k", "-shortest", str(path)]
    proc = subprocess.Popen(cmd, stdin=subprocess.PIPE)
    offset = 0.0
    for i in range(int(duration * FPS)):
        t = i / FPS
        offset += _speed(t)
        x = int(offset) % (W * 2)
        frame = tile[:, x:x + W].copy()
        cv2.rectangle(frame, (W // 2 - 40, H // 2 + 20), (W // 2 + 40, H // 2 + 60), (30, 30, 200), -1)
        if 95.0 <= t < 95.4:  # crash shake
            dx, dy = int(rng.integers(-14, 15)), int(rng.integers(-14, 15))
            frame = np.roll(frame, (dy, dx), axis=(0, 1))
        if 40.0 <= t < 42.0:  # explosion flash then fireball glow
            k = max(0.0, 1.0 - (t - 40.0) / 2.0)
            colour = np.array([60, 160, 255], np.float32) if t > 40.15 else np.array([255, 255, 255], np.float32)
            frame = (frame * (1 - 0.8 * k) + colour * 0.8 * k).astype(np.uint8)
        if 70.0 <= t < 78.0:  # police lights alternate at 4 Hz
            left = int(t * 4) % 2 == 0
            ov = frame.copy()
            if left:
                ov[:, : W // 2, 2] = 255
            else:
                ov[:, W // 2 :, 0] = 255
            frame = cv2.addWeighted(frame, 0.45, ov, 0.55, 0)
        if 20.0 <= t < 24.0 and int(t * 6) % 2 == 0:  # muzzle flicker
            frame[: H // 3] = np.clip(frame[: H // 3].astype(np.int16) + 25, 0, 255).astype(np.uint8)
        frame[hud_mask] = 255
        proc.stdin.write(frame.tobytes())
    proc.stdin.close()
    if proc.wait() != 0:
        raise RuntimeError("ffmpeg failed to encode the synthetic video")
    wav.unlink(missing_ok=True)
    return path
