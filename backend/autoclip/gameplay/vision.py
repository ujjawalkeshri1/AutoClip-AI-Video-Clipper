"""Optional vision-language and text-LLM refinement.

Runs only on the best few candidates, because it is by far the slowest step.
Nothing here is required: every function degrades to "skipped" with a reason.
A generic vision model does not know GTA 6; it sees pixels. Its output is
treated as one more piece of evidence, and the explanation says so.
"""

from __future__ import annotations

import base64
import json
import logging
import re
import subprocess
from collections.abc import Callable
from pathlib import Path

import httpx

from ..config import VisionSettings
from ..pipeline import ffmpeg
from .candidates import CATEGORIES, Candidate

log = logging.getLogger(__name__)

VOCAB = {
    "explosion": "EXPLOSION", "car crash": "CRASH", "police": "POLICE_CHASE",
    "shootout": "SHOOTOUT", "npc interaction": "NPC_INTERACTION", "mission or cutscene": "MISSION_HIGHLIGHT",
    "funny physics": "FUNNY", "driving": "ACTION", "combat": "SHOOTOUT",
}
PROMPT = (
    "You are looking at {n} frames, in time order, from a video game clip. Describe only what is "
    "visible. Respond with ONLY a JSON object, no prose, with keys: "
    '"labels" (a list chosen from: ' + ", ".join(VOCAB) + ", calm or idle, menu or loading), "
    '"description" (one plain sentence of what is visible), "interest" (integer 0-10: how visually '
    'interesting a viewer would find it), "unexpected" (true/false: does something unusual happen), '
    '"title" (a truthful title under 60 characters). Do not guess story or character names.'
)


def check_available(cfg: VisionSettings) -> tuple[bool, str]:
    try:
        r = httpx.get(f"{cfg.base_url.rstrip('/')}/api/tags", timeout=4.0)
        r.raise_for_status()
        names = [m.get("name", "") for m in r.json().get("models", [])]
    except Exception as exc:  # noqa: BLE001
        return False, f"Ollama not reachable at {cfg.base_url} ({exc.__class__.__name__})"
    base = cfg.model.split(":")[0]
    if not any(n == cfg.model or n.split(":")[0] == base for n in names):
        return False, f"model '{cfg.model}' is not pulled (run: ollama pull {cfg.model})"
    return True, "ok"


def keyframe_times(c: Candidate, n: int) -> list[float]:
    pts = [c.start + (c.peak - c.start) * 0.5, c.peak, c.peak + min(c.end - c.peak, 6.0) * 0.8]
    if n >= 4:
        pts.insert(0, c.start + 1.0)
    return [round(max(c.start, min(c.end - 0.1, t)), 2) for t in pts[:n]]


def grab_frame(video: Path, t: float, width: int) -> bytes:
    r = subprocess.run(
        [ffmpeg.ffmpeg_path(), "-v", "error", "-ss", f"{t:.3f}", "-i", str(video), "-frames:v", "1",
         "-vf", f"scale={width}:-2", "-f", "image2pipe", "-c:v", "mjpeg", "-q:v", "4", "-"],
        capture_output=True, timeout=60)
    if r.returncode != 0 or not r.stdout:
        raise RuntimeError(f"could not grab frame at {t:.1f}s")
    return r.stdout


def parse_json(text: str) -> dict:
    m = re.search(r"\{.*\}", text, re.S)
    if not m:
        raise ValueError("no JSON object in response")
    return json.loads(m.group(0))


def apply_vision(c: Candidate, data: dict) -> None:
    labels = [str(x).lower() for x in data.get("labels", []) if isinstance(x, str)]
    cats = [VOCAB[l] for l in labels if l in VOCAB]
    try:
        interest = max(0, min(10, int(float(data.get("interest", 5)))))
    except (TypeError, ValueError):
        interest = 5
    c.meta["vision"] = {
        "labels": labels, "description": str(data.get("description", ""))[:240],
        "interest": interest, "unexpected": bool(data.get("unexpected", False)),
        "title": str(data.get("title", ""))[:90],
    }
    if "calm or idle" in labels or "menu or loading" in labels:
        c.meta["vision"]["interest"] = min(interest, 3)
        c.evidence.append("vision model sees calm/idle or a menu")
        c.confidence = max(0.0, c.confidence - 0.2)
        return
    if cats:
        if c.category in ("ACTION", "SURPRISE") and cats[0] != "ACTION":
            c.category, c.confidence = cats[0], max(c.confidence, 0.6)
            c.evidence = [f"vision model labels it: {', '.join(labels[:2])}"]
        elif c.category in cats:
            c.confidence = min(1.0, c.confidence + 0.15)
            c.evidence.append("vision model agrees")
        for k in cats:
            if k != c.category and k != "ACTION" and k not in c.tags:
                c.tags.append(k)


def run_vision(video: Path, cands: list[Candidate], cfg: VisionSettings, *,
               on_progress: Callable[[float], None] | None = None,
               cancelled: Callable[[], bool] | None = None) -> str:
    """Annotate the top candidates. Returns a short status string for the log."""
    ok, why = check_available(cfg)
    if not ok:
        return f"vision skipped: {why}"
    chosen = sorted(cands, key=lambda c: -c.score)[: cfg.max_candidates]
    done = 0
    for c in chosen:
        if cancelled and cancelled():
            break
        try:
            frames = [base64.b64encode(grab_frame(video, t, cfg.keyframe_width)).decode()
                      for t in keyframe_times(c, cfg.keyframes)]
            r = httpx.post(f"{cfg.base_url.rstrip('/')}/api/generate", timeout=cfg.timeout_s, json={
                "model": cfg.model, "prompt": PROMPT.format(n=len(frames)), "images": frames,
                "stream": False, "format": "json", "options": {"temperature": 0.1}})
            r.raise_for_status()
            apply_vision(c, parse_json(r.json().get("response", "")))
        except Exception as exc:  # noqa: BLE001 - one bad clip must not stop the rest
            log.warning("Vision failed for candidate at %.0fs: %s", c.peak, exc)
            c.meta["vision_error"] = str(exc)[:160]
        done += 1
        if on_progress:
            on_progress(done / len(chosen))
    return f"vision ran on {done} candidate(s) with {cfg.model}"


LLM_SYSTEM = (
    "You help title short gaming clips. You are given EVIDENCE a detector measured, not a video. "
    "Never invent events that the evidence does not support. Respond with ONLY JSON: "
    '{"clips":[{"id":<int>,"interest":<0-10>,"titles":[3 titles under 70 chars],"hook":"<=8 words"}]}'
)


async def llm_refine(cands: list[Candidate], provider, config) -> str:
    """Ask a text LLM for truthful titles/hooks and an interest rating."""
    items = [{
        "id": i, "category": c.category, "confidence": round(c.confidence, 2), "evidence": c.evidence,
        "duration_s": round(c.duration), "transcript": c.transcript_text[:400],
        "vision": (c.meta.get("vision") or {}).get("description", ""),
    } for i, c in enumerate(cands)]
    try:
        raw = await provider.complete_text(LLM_SYSTEM, json.dumps(items), config)
        data = parse_json(raw)
    except Exception as exc:  # noqa: BLE001
        return f"LLM refinement skipped: {exc.__class__.__name__}"
    for row in data.get("clips", []):
        try:
            c = cands[int(row["id"])]
        except (KeyError, ValueError, IndexError, TypeError):
            continue
        if isinstance(row.get("titles"), list):
            c.meta["llm_titles"] = [str(t)[:95] for t in row["titles"] if t][:3]
        if row.get("hook"):
            c.meta["llm_hook"] = str(row["hook"])[:80]
        try:
            c.meta["llm_interest"] = max(0, min(10, int(float(row["interest"]))))
        except (KeyError, ValueError, TypeError):
            pass
    return "LLM refinement applied"
