"""Dashboard, highlights browser, batch generation and exported-clip management."""

from __future__ import annotations

import asyncio
import logging
import os
import subprocess
import sys
import threading
from pathlib import Path

from fastapi import APIRouter, HTTPException

from .. import accel, paths
from ..config import load as load_settings
from ..config import save as save_settings
from ..db import connection, store
from ..jobs.queue import queue
from .clips import render_clip
from .schemas import BatchActionIn, BatchExportIn, ClipOut, ExportRequestIn

log = logging.getLogger(__name__)
router = APIRouter(prefix="/api", tags=["gameplay"])

#: In-memory batch progress. A batch is a convenience over single exports, so a
#: restart simply forgets it; the rendered files and their records remain.
_batches: dict[str, dict] = {}
_batch_lock = threading.Lock()


def _rows(sql: str, args: tuple = ()) -> list:
    with connection() as conn:
        return conn.execute(sql, args).fetchall()


@router.get("/stats")
async def stats() -> dict:
    def work() -> dict:
        videos = _rows("SELECT COUNT(DISTINCT source_id) FROM jobs WHERE status='done'")[0][0]
        highlights = _rows("SELECT COUNT(*) FROM clips")[0][0]
        generated = _rows("SELECT COUNT(DISTINCT clip_id) FROM exports")[0][0]
        approved = _rows("SELECT COUNT(*) FROM clips WHERE status='kept'")[0][0]
        top = _rows(
            "SELECT c.id, c.job_id, c.title, c.score, c.category, c.status, s.title AS source "
            "FROM clips c JOIN jobs j ON j.id=c.job_id JOIN sources s ON s.id=j.source_id "
            "ORDER BY c.score DESC LIMIT 6")
        recent = [j for j in store.list_jobs(limit=6)]
        out_recent = []
        for j in recent:
            src = store.get_source(j.source_id)
            out_recent.append({"id": j.id, "title": (src.title or src.filename) if src else "", "status": j.status,
                               "progress": j.progress, "stage": j.current_stage, "created_at": j.created_at,
                               "mode": (j.settings or {}).get("analysis_mode", "speech"),
                               "highlights": len(store.list_clips(j.id))})
        return {
            "videos_processed": videos, "highlights_detected": highlights, "clips_generated": generated,
            "approved": approved,
            "top_clips": [{"id": r["id"], "job_id": r["job_id"], "title": r["title"], "score": r["score"],
                           "category": r["category"], "status": r["status"], "source": r["source"]} for r in top],
            "recent_jobs": out_recent,
        }

    data = await asyncio.to_thread(work)
    st = queue.status()
    data["queue"] = {"running_job_id": st.running_job_id, "queued": st.queued,
                     "paused": bool(st.running_job_id and queue.is_paused(st.running_job_id))}
    return data


@router.get("/highlights", response_model=list[ClipOut])
async def highlights(job_id: str | None = None, category: str | None = None,
                     status: str | None = None, min_score: int = 0) -> list[ClipOut]:
    def work() -> list[ClipOut]:
        sql, args = "SELECT id FROM clips WHERE score >= ?", [min_score]
        if job_id:
            sql += " AND job_id = ?"; args.append(job_id)
        if category:
            sql += " AND category = ?"; args.append(category)
        if status:
            sql += " AND status = ?"; args.append(status)
        sql += " ORDER BY score DESC, rank ASC LIMIT 500"
        out = []
        for r in _rows(sql, tuple(args)):
            clip = store.get_clip(r["id"])
            out.append(ClipOut.of(clip, edit=store.get_clip_edit(clip.id), exports=store.list_exports(clip.id)))
        return out

    return await asyncio.to_thread(work)


@router.post("/clips/batch/action")
async def batch_action(payload: BatchActionIn) -> dict:
    status = {"approve": "kept", "reject": "discarded", "reset": "candidate"}[payload.action]
    for cid in payload.clip_ids:
        await asyncio.to_thread(store.update_clip, cid, status=status)
    return {"updated": len(payload.clip_ids), "status": status}


@router.post("/clips/batch/export", status_code=202)
async def batch_export(payload: BatchExportIn) -> dict:
    """Render several clips in the background; poll ``/api/batches/{id}``."""
    ids = list(payload.clip_ids)
    if payload.approved_in_job:
        clips = await asyncio.to_thread(store.list_clips, payload.approved_in_job)
        ids += [c.id for c in clips if c.status == "kept" and c.id not in ids]
    if not ids:
        raise HTTPException(status_code=400, detail="No clips selected (approve some first).")

    import uuid

    batch_id = uuid.uuid4().hex[:12]
    state = {"id": batch_id, "total": len(ids), "done": 0, "current": "", "errors": [], "exports": [],
             "finished": False}
    with _batch_lock:
        _batches[batch_id] = state
    req = ExportRequestIn(ratio=payload.ratio, style=payload.style, framing=payload.framing,
                          hook_text=payload.hook_text, music_id=payload.music_id,
                          music_volume=payload.music_volume, game_volume=payload.game_volume, duck=payload.duck)

    def run() -> None:
        for cid in ids:
            clip = store.get_clip(cid)
            state["current"] = clip.title if clip else cid
            try:
                state["exports"].append(render_clip(cid, req).model_dump())
            except HTTPException as exc:
                state["errors"].append({"clip_id": cid, "error": str(exc.detail)})
            except Exception as exc:  # noqa: BLE001 - one failure must not stop the batch
                log.exception("Batch export failed for %s", cid)
                state["errors"].append({"clip_id": cid, "error": str(exc)[:300]})
            state["done"] += 1
        state["finished"] = True
        state["current"] = ""

    threading.Thread(target=run, name=f"batch-{batch_id}", daemon=True).start()
    return state


@router.get("/batches/{batch_id}")
async def batch_status(batch_id: str) -> dict:
    state = _batches.get(batch_id)
    if state is None:
        raise HTTPException(status_code=404, detail="Unknown batch (the app may have restarted).")
    return state


@router.get("/exports")
async def list_all_exports() -> list[dict]:
    def work() -> list[dict]:
        rows = _rows(
            "SELECT e.id, e.clip_id, e.path, e.ratio, e.style, e.size_bytes, e.created_at, "
            "c.title, c.score, c.category, c.start_s, c.end_s, c.job_id, c.meta_json "
            "FROM exports e JOIN clips c ON c.id=e.clip_id ORDER BY e.created_at DESC LIMIT 300")
        import json

        out = []
        for r in rows:
            p = Path(r["path"])
            w, h = {"9:16": (1080, 1920), "1:1": (1080, 1080), "16:9": (1920, 1080)}.get(r["ratio"], (0, 0))
            meta = json.loads(r["meta_json"] or "{}")
            out.append({
                "id": r["id"], "clip_id": r["clip_id"], "job_id": r["job_id"], "filename": p.name,
                "path": str(p), "exists": p.exists(), "size_bytes": r["size_bytes"], "ratio": r["ratio"],
                "resolution": f"{w}x{h}", "duration_s": round(r["end_s"] - r["start_s"], 1),
                "title": r["title"], "score": r["score"], "category": r["category"],
                "hashtags": meta.get("hashtags", []), "created_at": r["created_at"],
                "download_url": f"/api/exports/{r['id']}/download",
                "has_thumbnail": p.with_suffix(".jpg").exists(),
            })
        return out

    return await asyncio.to_thread(work)


@router.get("/exports/{export_id}/thumbnail")
async def export_thumbnail(export_id: str):
    from fastapi.responses import FileResponse

    rec = await asyncio.to_thread(store.get_export, export_id)
    if rec is None or not Path(rec.path).with_suffix(".jpg").exists():
        raise HTTPException(status_code=404, detail="No thumbnail.")
    return FileResponse(Path(rec.path).with_suffix(".jpg"), media_type="image/jpeg")


@router.post("/exports/{export_id}/reveal")
async def reveal_export(export_id: str) -> dict:
    """Open the folder containing an export in the system file manager (local app only)."""
    rec = await asyncio.to_thread(store.get_export, export_id)
    if rec is None:
        raise HTTPException(status_code=404, detail="Export not found.")
    p = Path(rec.path)
    if not p.exists():
        raise HTTPException(status_code=410, detail="The file has been moved or deleted.")
    # Only paths inside the exports folder are ever opened.
    if paths.exports_dir().resolve() not in p.resolve().parents:
        raise HTTPException(status_code=400, detail="Refusing to open a path outside the exports folder.")
    try:
        if sys.platform == "win32":
            subprocess.Popen(["explorer", "/select,", str(p)])
        elif sys.platform == "darwin":
            subprocess.Popen(["open", "-R", str(p)])
        else:
            subprocess.Popen(["xdg-open", str(p.parent)])
    except OSError as exc:
        raise HTTPException(status_code=500, detail=f"Could not open the folder: {exc}") from exc
    return {"opened": str(p.parent)}


@router.get("/system/accel")
async def system_accel() -> dict:
    r = await asyncio.to_thread(accel.report)
    return r.__dict__


@router.post("/gameplay/preset")
async def set_preset(name: str) -> dict:
    from ..gameplay.presets import apply_preset

    s = load_settings()
    try:
        apply_preset(s, name)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    save_settings(s)
    return {"preset": name, "whisper_model": s.whisper.model, "sample_fps": s.gameplay.sample_fps,
            "transcribe_scope": s.gameplay.transcribe_scope}
