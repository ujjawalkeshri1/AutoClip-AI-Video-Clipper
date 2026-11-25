"""Source ingestion endpoints."""

from __future__ import annotations

import asyncio
import logging
import shutil
import tempfile
from pathlib import Path

from fastapi import APIRouter, File, HTTPException, UploadFile

from ..config import load as load_settings
from ..db import store
from ..pipeline import ingest
from .schemas import SourceOut, YouTubeIngestIn

log = logging.getLogger(__name__)

router = APIRouter(prefix="/api/sources", tags=["sources"])

#: Uploads stream to disk in chunks rather than being buffered whole — source
#: videos routinely exceed available RAM.
UPLOAD_CHUNK = 4 * 1024 * 1024


@router.get("", response_model=list[SourceOut])
async def list_sources(limit: int = 50) -> list[SourceOut]:
    sources = await asyncio.to_thread(store.list_sources, limit)
    return [SourceOut.of(s) for s in sources]


@router.get("/{source_id}", response_model=SourceOut)
async def get_source(source_id: str) -> SourceOut:
    source = await asyncio.to_thread(store.get_source, source_id)
    if source is None:
        raise HTTPException(status_code=404, detail="Source not found.")
    return SourceOut.of(source)


@router.get("/{source_id}/info")
async def source_info(source_id: str) -> dict:
    """Technical details of a source: container, codecs, FPS, audio layout, file size."""
    import json
    import subprocess

    from ..pipeline import ffmpeg

    source = await asyncio.to_thread(store.get_source, source_id)
    if source is None:
        raise HTTPException(status_code=404, detail="Source not found.")
    path = Path(source.path)
    if not path.exists():
        raise HTTPException(status_code=410, detail="The source file is missing.")

    def probe() -> dict:
        r = subprocess.run(
            [ffmpeg.ffprobe_path(), "-v", "error", "-show_streams", "-show_format", "-of", "json", str(path)],
            capture_output=True, text=True, timeout=60)
        return json.loads(r.stdout or "{}")

    data = await asyncio.to_thread(probe)
    streams = data.get("streams", [])
    v = next((x for x in streams if x.get("codec_type") == "video"), None)
    a = next((x for x in streams if x.get("codec_type") == "audio"), None)

    def fps(x: dict) -> float | None:
        try:
            n, d = x.get("avg_frame_rate", "0/1").split("/")
            return round(float(n) / float(d), 2) if float(d) else None
        except (ValueError, ZeroDivisionError):
            return None

    return {
        "file_size_bytes": path.stat().st_size,
        "container": (data.get("format", {}).get("format_long_name") or "").split(",")[0],
        "duration_s": source.duration_s,
        "video": None if v is None else {
            "codec": v.get("codec_name"), "width": v.get("width"), "height": v.get("height"), "fps": fps(v),
            "bit_rate": int(v["bit_rate"]) if v.get("bit_rate") else None},
        "audio": None if a is None else {
            "codec": a.get("codec_name"), "channels": a.get("channels"),
            "sample_rate": int(a["sample_rate"]) if a.get("sample_rate") else None,
            "bit_rate": int(a["bit_rate"]) if a.get("bit_rate") else None},
    }


@router.post("/youtube", response_model=SourceOut, status_code=201)
async def ingest_youtube(payload: YouTubeIngestIn) -> SourceOut:
    """Download a YouTube video and register it as a source."""
    if not ingest.is_youtube_url(payload.url):
        raise HTTPException(status_code=400, detail="That is not a YouTube URL.")

    settings = load_settings().ingest
    if payload.cookies_from_browser is not None:
        settings = settings.model_copy(
            update={"cookies_from_browser": payload.cookies_from_browser}
        )

    try:
        source = await asyncio.to_thread(ingest.ingest_youtube, payload.url, settings)
    except ingest.IngestError as exc:
        # 422 rather than 500: the request was well-formed, but the content
        # can't be fetched, and the hint tells the user what to change.
        raise HTTPException(
            status_code=422, detail={"message": str(exc), "hint": exc.hint}
        ) from exc

    await asyncio.to_thread(store.create_source, source)
    return SourceOut.of(source)


@router.post("/upload", response_model=SourceOut, status_code=201)
async def upload_source(file: UploadFile = File(...)) -> SourceOut:
    """Accept a media upload and register it as a source."""
    filename = Path(file.filename or "upload")
    suffix = filename.suffix.lower()
    if suffix not in ingest.ACCEPTED_SUFFIXES:
        raise HTTPException(
            status_code=415,
            detail={
                "message": f"{suffix or 'This file'} is not a supported format.",
                "hint": f"Accepted: {', '.join(sorted(ingest.ACCEPTED_SUFFIXES))}",
            },
        )

    # Stage to a temp file first so a failed or abandoned upload never leaves a
    # half-written file in the media store.
    with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as staging:
        staging_path = Path(staging.name)
        try:
            while chunk := await file.read(UPLOAD_CHUNK):
                staging.write(chunk)
        except Exception as exc:
            staging_path.unlink(missing_ok=True)
            raise HTTPException(status_code=500, detail="Upload failed.") from exc

    try:
        source = await asyncio.to_thread(
            ingest.ingest_file, staging_path, move=True, title=filename.stem
        )
    except ingest.IngestError as exc:
        staging_path.unlink(missing_ok=True)
        raise HTTPException(
            status_code=422, detail={"message": str(exc), "hint": exc.hint}
        ) from exc
    finally:
        shutil.rmtree(staging_path.parent / staging_path.name, ignore_errors=True)

    await asyncio.to_thread(store.create_source, source)
    return SourceOut.of(source)
