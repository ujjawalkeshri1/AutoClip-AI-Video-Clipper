"""On-demand clip generation helpers for gameplay clips."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

from ..db.models import Clip, Source
from ..pipeline import ffmpeg
from ..pipeline.reframe.croppath import CropPath
from . import framing


def framing_cache(workspace_root: Path, clip: Clip, mode: str, ratio: str) -> Path:
    key = f"{clip.id}_{mode}_{ratio.replace(':', 'x')}_{clip.start_s:.2f}_{clip.end_s:.2f}"
    return workspace_root / "gameplay" / "framing" / f"{key}.json"


def frame_clip(workspace_root: Path, clip: Clip, source: Source, ratio: str, mode: str
               ) -> tuple[CropPath, framing.FramingDecision]:
    """Compute (or load cached) vertical framing for a clip."""
    cache = framing_cache(workspace_root, clip, mode, ratio)
    if cache.exists():
        data = json.loads(cache.read_text(encoding="utf-8"))
        return CropPath.from_dict(data["crop_path"]), framing.FramingDecision(**data["decision"])
    path, decision = framing.build_gameplay_crop_path(
        Path(source.path), start_s=clip.start_s, end_s=clip.end_s, mode=mode, ratio=ratio,
        source_duration=source.duration_s)
    cache.parent.mkdir(parents=True, exist_ok=True)
    cache.write_text(json.dumps({"crop_path": path.to_dict(), "decision": decision.__dict__}), encoding="utf-8")
    # The preview endpoint reads the single per-clip crop path; keep it in step.
    (workspace_root / "crops").mkdir(parents=True, exist_ok=True)
    path.save(workspace_root / "crops" / f"{clip.id}.json")
    return path, decision


def grab_thumbnail(video: Path, t: float, dest: Path, width: int = 640) -> Path:
    dest.parent.mkdir(parents=True, exist_ok=True)
    if dest.exists() and dest.stat().st_size > 0:
        return dest
    r = subprocess.run(
        [ffmpeg.ffmpeg_path(), "-v", "error", "-y", "-ss", f"{max(0.0, t):.3f}", "-i", str(video),
         "-frames:v", "1", "-vf", f"scale={width}:-2", "-q:v", "3", str(dest)],
        capture_output=True, timeout=60)
    if r.returncode != 0 or not dest.exists():
        raise RuntimeError("could not extract thumbnail")
    return dest


def write_sidecars(mp4: Path, clip: Clip, source: Source, *, framing_mode: str = "") -> None:
    """Thumbnail (JPG) and metadata (JSON) next to the exported video."""
    meta = clip.meta or {}
    try:
        grab_thumbnail(Path(source.path), float(meta.get("thumbnail_s", clip.start_s)), mp4.with_suffix(".jpg"), 1080)
    except Exception:  # noqa: BLE001 - a missing thumbnail must not fail the export
        pass
    info = {
        "file": mp4.name, "source_video": source.title or source.filename or "", "category": meta.get("category_label", clip.category),
        "score": clip.score, "score_note": meta.get("score_note", ""), "factors": clip.factors,
        "points": meta.get("points", {}), "start_s": clip.start_s, "end_s": clip.end_s,
        "duration_s": round(clip.end_s - clip.start_s, 2), "title_options": meta.get("titles", [clip.title]),
        "chosen_title": clip.title, "description": meta.get("description", ""),
        "hashtags": meta.get("hashtags", []), "hook": meta.get("hook", ""), "why_selected": clip.reason,
        "evidence": meta.get("evidence", []), "framing": framing_mode,
    }
    mp4.with_suffix(".json").write_text(json.dumps(info, indent=2, ensure_ascii=False), encoding="utf-8")
