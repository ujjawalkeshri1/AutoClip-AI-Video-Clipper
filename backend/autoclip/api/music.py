"""Background-music library: upload, list, preview and delete tracks.

Tracks are stored in ``<data folder>/music``. Only upload music you have the right to use:
this app does not check licences and does not supply any music.
"""

from __future__ import annotations

import asyncio
import json
import re
import uuid
from pathlib import Path

from fastapi import APIRouter, File, HTTPException, UploadFile
from fastapi.responses import FileResponse

from .. import paths
from ..pipeline import ffmpeg

router = APIRouter(prefix="/api/music", tags=["music"])

ALLOWED = {".mp3", ".wav", ".m4a", ".aac", ".flac", ".ogg", ".opus"}
MAX_BYTES = 200 * 1024 * 1024
_ID = re.compile(r"^[0-9a-f]{12}$")


def track_path(track_id: str) -> Path | None:
    """The stored audio file for ``track_id`` (validated: ids never contain path separators)."""
    if not _ID.match(track_id or ""):
        return None
    for p in paths.music_dir().glob(f"{track_id}.*"):
        if p.suffix.lower() in ALLOWED:
            return p
    return None


def _describe(p: Path) -> dict:
    meta_file = p.with_suffix(".json")
    meta = json.loads(meta_file.read_text(encoding="utf-8")) if meta_file.exists() else {}
    return {"id": p.stem, "name": meta.get("name", p.name), "duration_s": meta.get("duration_s", 0.0),
            "size_bytes": p.stat().st_size}


@router.get("")
async def list_tracks() -> list[dict]:
    def work() -> list[dict]:
        d = paths.music_dir()
        if not d.exists():
            return []
        return sorted((_describe(p) for p in d.iterdir() if p.suffix.lower() in ALLOWED and _ID.match(p.stem)),
                      key=lambda t: t["name"].lower())

    return await asyncio.to_thread(work)


@router.post("", status_code=201)
async def upload_track(file: UploadFile = File(...)) -> dict:
    name = Path(file.filename or "track").name
    ext = Path(name).suffix.lower()
    if ext not in ALLOWED:
        raise HTTPException(status_code=415, detail=f"Unsupported audio type {ext or '(none)'}. "
                            f"Use one of: {', '.join(sorted(ALLOWED))}.")
    paths.music_dir().mkdir(parents=True, exist_ok=True)
    track_id = uuid.uuid4().hex[:12]
    dest = paths.music_dir() / f"{track_id}{ext}"
    written = 0
    try:
        with open(dest, "wb") as out:
            while chunk := await file.read(1 << 20):
                written += len(chunk)
                if written > MAX_BYTES:
                    raise HTTPException(status_code=413, detail="That file is larger than 200 MB.")
                out.write(chunk)
        try:
            info = await asyncio.to_thread(ffmpeg.probe, dest)
        except Exception as exc:  # noqa: BLE001 - any probe failure means "not usable audio"
            raise HTTPException(status_code=422, detail=f"{name} could not be read as audio ({str(exc)[:160]}).") from exc
        if not info.has_audio or info.duration_s < 1.0:
            raise HTTPException(status_code=422, detail=f"{name} has no usable audio (it must be at least 1 second long).")
        dest.with_suffix(".json").write_text(json.dumps({"name": name, "duration_s": round(info.duration_s, 2)}),
                                             encoding="utf-8")
    except BaseException:
        dest.unlink(missing_ok=True)
        dest.with_suffix(".json").unlink(missing_ok=True)
        raise
    return _describe(dest)


@router.get("/{track_id}/file")
async def track_file(track_id: str) -> FileResponse:
    p = track_path(track_id)
    if p is None:
        raise HTTPException(status_code=404, detail="Track not found.")
    return FileResponse(p)


@router.delete("/{track_id}", status_code=204)
async def delete_track(track_id: str) -> None:
    p = track_path(track_id)
    if p is None:
        raise HTTPException(status_code=404, detail="Track not found.")
    p.unlink(missing_ok=True)
    p.with_suffix(".json").unlink(missing_ok=True)
    from ..config import load, save

    settings = load()
    if settings.audio.music_id == track_id:  # never leave a dangling default
        settings.audio.music_id = ""
        save(settings)
