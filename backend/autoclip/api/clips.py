"""Clip review, editing, preview media, and export."""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse

from .. import paths
from ..config import load as load_settings
from ..db import store
from ..db.models import Export, new_id
from ..pipeline import captions as captions_module
from ..pipeline import export as export_module
from ..pipeline.reframe.croppath import CropPath, centre_crop
from ..pipeline.runner import JobWorkspace
from ..pipeline.transcript import Transcript, Word
from .schemas import (
    CaptionPatchIn,
    CaptionStyleOut,
    ClipOut,
    ClipPatchIn,
    ExportOut,
    ExportRequestIn,
    WordOut,
)

log = logging.getLogger(__name__)

router = APIRouter(prefix="/api", tags=["clips"])


def _clip_out(clip) -> ClipOut:
    edit = store.get_clip_edit(clip.id)
    exports = store.list_exports(clip.id)
    return ClipOut.of(clip, edit=edit, exports=exports)


async def list_clips_for_job(job_id: str) -> list[ClipOut]:
    job = await asyncio.to_thread(store.get_job, job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found.")
    clips = await asyncio.to_thread(store.list_clips, job_id)
    return [await asyncio.to_thread(_clip_out, clip) for clip in clips]


@router.get("/clips/{clip_id}", response_model=ClipOut)
async def get_clip(clip_id: str) -> ClipOut:
    clip = await asyncio.to_thread(store.get_clip, clip_id)
    if clip is None:
        raise HTTPException(status_code=404, detail="Clip not found.")
    return await asyncio.to_thread(_clip_out, clip)


@router.get("/clips/{clip_id}/words", response_model=list[WordOut])
async def clip_words(clip_id: str) -> list[WordOut]:
    """Words for a clip — the caption editor's source of truth.

    Returns the user's edited words when they exist, falling back to the
    transcript's originals.
    """
    clip = await asyncio.to_thread(store.get_clip, clip_id)
    if clip is None:
        raise HTTPException(status_code=404, detail="Clip not found.")

    edit = await asyncio.to_thread(store.get_clip_edit, clip_id)
    if edit is not None and edit.edited_words:
        return [WordOut(**word) for word in edit.edited_words]

    transcript = await asyncio.to_thread(_load_transcript, clip.job_id)
    if clip.category:
        # Gameplay clips are often silent and their transcript is sparse, so an
        # index range cannot express "no words". Select by time instead.
        words = [w for w in transcript.words if clip.start_s <= w.start < clip.end_s]
    else:
        words = transcript.slice(clip.start_word, clip.end_word)
    return [WordOut(text=w.text, start=w.start, end=w.end, speaker=w.speaker) for w in words]


@router.get("/clips/{clip_id}/crop-path")
async def clip_crop_path(clip_id: str) -> dict:
    """The computed crop path for a clip, so the preview can match the export.

    Without this the review player can only centre-crop, which on a tracked shot
    shows different framing from the file that gets rendered — sometimes with
    the speaker half out of frame. Reviewing framing against the wrong framing
    is worse than not previewing it at all.
    """
    clip = await asyncio.to_thread(store.get_clip, clip_id)
    if clip is None:
        raise HTTPException(status_code=404, detail="Clip not found.")

    cached = JobWorkspace(clip.job_id).crop_path(clip.id)
    if not cached.exists():
        # Audio-only sources and pre-reframe jobs legitimately have none; the
        # client falls back to a centre crop.
        raise HTTPException(status_code=404, detail="No crop path for this clip yet.")

    return (await asyncio.to_thread(CropPath.load, cached)).to_dict()


@router.patch("/clips/{clip_id}", response_model=ClipOut)
async def patch_clip(clip_id: str, payload: ClipPatchIn) -> ClipOut:
    """Trim, retitle, or keep/discard a clip.

    A trim re-derives the word range from the new times, so captions stay in
    step with the boundaries without the client having to compute indices.
    """
    clip = await asyncio.to_thread(store.get_clip, clip_id)
    if clip is None:
        raise HTTPException(status_code=404, detail="Clip not found.")

    start_s = payload.start_s if payload.start_s is not None else clip.start_s
    end_s = payload.end_s if payload.end_s is not None else clip.end_s
    if end_s <= start_s:
        raise HTTPException(status_code=400, detail="End must come after start.")

    trimmed = payload.start_s is not None or payload.end_s is not None
    start_word = end_word = None

    if trimmed:
        transcript = await asyncio.to_thread(_load_transcript, clip.job_id)
        first, last = transcript.indices_in_range(start_s, end_s)
        start_word, end_word = first, max(first, last - 1)

    await asyncio.to_thread(
        store.update_clip,
        clip_id,
        start_s=start_s if trimmed else None,
        end_s=end_s if trimmed else None,
        start_word=start_word,
        end_word=end_word,
        title=payload.title,
        status=payload.status,
        user_trimmed=True if trimmed else None,
    )

    updated = await asyncio.to_thread(store.get_clip, clip_id)
    return await asyncio.to_thread(_clip_out, updated)


@router.patch("/clips/{clip_id}/captions", response_model=ClipOut)
async def patch_captions(clip_id: str, payload: CaptionPatchIn) -> ClipOut:
    """Store word-level caption edits and style choices for a clip."""
    clip = await asyncio.to_thread(store.get_clip, clip_id)
    if clip is None:
        raise HTTPException(status_code=404, detail="Clip not found.")

    if payload.caption_style is not None:
        try:
            captions_module.get_style(payload.caption_style)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    existing = await asyncio.to_thread(store.get_clip_edit, clip_id)
    from ..db.models import ClipEdit

    edit = ClipEdit(
        clip_id=clip_id,
        edited_words=(
            [w.model_dump() for w in payload.words]
            if payload.words is not None
            else (existing.edited_words if existing else None)
        ),
        caption_style=payload.caption_style or (existing.caption_style if existing else "bold_pop"),
        ratio=payload.ratio or (existing.ratio if existing else "9:16"),
    )
    await asyncio.to_thread(store.upsert_clip_edit, edit)

    return await asyncio.to_thread(_clip_out, clip)


def _resolve_audio(payload, settings) -> tuple[Path | None, float, float, bool]:
    """Per-export overrides first, then the saved audio settings."""
    from .music import track_path

    a = settings.audio
    music_id = a.music_id if payload.music_id is None else ("" if payload.music_id == "none" else payload.music_id)
    path = None
    if music_id:
        path = track_path(music_id)
        if path is None:
            raise HTTPException(status_code=400, detail="The selected music track no longer exists. "
                                "Choose another in Settings > Audio or upload it again.")
    return (path,
            a.music_volume if payload.music_volume is None else payload.music_volume,
            a.game_volume if payload.game_volume is None else payload.game_volume,
            a.duck if payload.duck is None else payload.duck)


def render_clip(clip_id: str, payload: ExportRequestIn, words_override: list[Word] | None = None) -> ExportOut:
    """Render one clip. Blocking; call from a thread. Raises HTTPException on failure."""
    clip = store.get_clip(clip_id)
    if clip is None:
        raise HTTPException(status_code=404, detail="Clip not found.")

    job = store.get_job(clip.job_id)
    source = store.get_source(job.source_id) if job else None
    if job is None or source is None:
        raise HTTPException(status_code=404, detail="The clip's source is missing.")

    try:
        style = captions_module.get_style(payload.style)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    if words_override is not None:
        words = words_override
    else:
        edit = store.get_clip_edit(clip_id)
        if edit is not None and edit.edited_words:
            words = [Word(text=w["text"], start=w["start"], end=w["end"], speaker=w.get("speaker"))
                     for w in edit.edited_words]
        else:
            transcript = _load_transcript(clip.job_id)
            if clip.category:
                words = [w for w in transcript.words if clip.start_s <= w.start < clip.end_s]
            else:
                words = transcript.slice(clip.start_word, clip.end_word)

    settings = load_settings()
    settings.export.write_srt = payload.write_srt

    workspace = JobWorkspace(clip.job_id)
    framing_mode = ""
    hook_text = ""
    if clip.category:
        from ..gameplay import service

        framing_mode = payload.framing or settings.gameplay.framing
        use_hook = settings.gameplay.hook_text if payload.hook_text is None else payload.hook_text
        if use_hook:
            hook_text = clip.hook or (clip.meta or {}).get("hook", "")
        try:
            crop_path, decision = service.frame_clip(workspace.root, clip, source, payload.ratio, framing_mode)
            framing_mode = decision.mode
        except Exception as exc:  # noqa: BLE001 - framing must never block an export
            log.warning("Gameplay framing failed (%s); using a centre crop.", exc)
            crop_path = _crop_path_for(workspace, clip, source, payload.ratio)
            framing_mode = "center"
    else:
        crop_path = _crop_path_for(workspace, clip, source, payload.ratio)

    destination = (
        paths.exports_dir()
        / clip.job_id
        / export_module.output_filename(clip.title or f"clip-{clip.rank}", payload.ratio)
    )

    music_path, music_volume, game_volume, duck = _resolve_audio(payload, settings)
    request = export_module.ExportRequest(
        source=Path(source.path),
        destination=destination,
        start_s=clip.start_s,
        end_s=clip.end_s,
        crop_path=crop_path,
        words=words,
        style=style,
        ratio=payload.ratio,
        hook_text=hook_text,
        music=music_path,
        music_volume=music_volume,
        game_volume=game_volume,
        duck=duck,
        source_has_audio=source.has_audio,
    )

    try:
        export_module.export_clip(request, work_dir=workspace.captions_dir, settings=settings.export)
    except export_module.ExportError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc

    if clip.category:
        from ..gameplay import service

        service.write_sidecars(destination, clip, source, framing_mode=framing_mode)

    record = Export(
        id=new_id(),
        clip_id=clip_id,
        path=str(destination),
        ratio=payload.ratio,
        style=style.key,
        size_bytes=destination.stat().st_size,
    )
    store.create_export(record)
    store.update_clip(clip_id, status="exported")
    return ExportOut.of(record)


@router.post("/clips/{clip_id}/export", response_model=ExportOut, status_code=201)
async def export_clip(clip_id: str, payload: ExportRequestIn) -> ExportOut:
    """Render one clip and return its download link."""
    return await asyncio.to_thread(render_clip, clip_id, payload)


@router.post("/clips/{clip_id}/framing")
async def preview_framing(clip_id: str, mode: str = "auto", ratio: str = "9:16") -> dict:
    """Compute the vertical framing for a clip (so the preview matches the export)."""
    from ..gameplay import framing as framing_module
    from ..gameplay import service

    if mode not in framing_module.MODES:
        raise HTTPException(status_code=400, detail=f"Unknown framing mode {mode!r}.")
    clip = await asyncio.to_thread(store.get_clip, clip_id)
    job = await asyncio.to_thread(store.get_job, clip.job_id) if clip else None
    source = await asyncio.to_thread(store.get_source, job.source_id) if job else None
    if clip is None or source is None:
        raise HTTPException(status_code=404, detail="Clip not found.")
    path, decision = await asyncio.to_thread(
        service.frame_clip, JobWorkspace(clip.job_id).root, clip, source, ratio, mode)
    return {"crop_path": path.to_dict(), "decision": decision.__dict__}


@router.get("/clips/{clip_id}/thumbnail")
async def clip_thumbnail(clip_id: str) -> FileResponse:
    from ..gameplay import service

    clip = await asyncio.to_thread(store.get_clip, clip_id)
    job = await asyncio.to_thread(store.get_job, clip.job_id) if clip else None
    source = await asyncio.to_thread(store.get_source, job.source_id) if job else None
    if clip is None or source is None:
        raise HTTPException(status_code=404, detail="Clip not found.")
    t = float((clip.meta or {}).get("thumbnail_s", clip.start_s))
    dest = JobWorkspace(clip.job_id).root / "thumbs" / f"{clip.id}.jpg"
    try:
        await asyncio.to_thread(service.grab_thumbnail, Path(source.path), t, dest)
    except RuntimeError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return FileResponse(dest, media_type="image/jpeg")


@router.get("/exports/{export_id}/download")
async def download_export(export_id: str) -> FileResponse:
    record = await asyncio.to_thread(store.get_export, export_id)
    if record is None:
        raise HTTPException(status_code=404, detail="Export not found.")

    path = Path(record.path)
    if not path.exists():
        raise HTTPException(status_code=410, detail="The exported file has been moved or deleted.")

    return FileResponse(path, media_type="video/mp4", filename=path.name)


@router.get("/jobs/{job_id}/media")
async def job_media(job_id: str) -> FileResponse:
    """Serve the source video, so the review player can scrub the original.

    Range requests are handled by FileResponse, which is what makes seeking in
    the preview player usable on a long source.
    """
    job = await asyncio.to_thread(store.get_job, job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found.")

    source = await asyncio.to_thread(store.get_source, job.source_id)
    if source is None or not Path(source.path).exists():
        raise HTTPException(status_code=404, detail="Source media not found.")

    return FileResponse(Path(source.path))


@router.get("/caption-styles", response_model=list[CaptionStyleOut])
async def caption_styles() -> list[CaptionStyleOut]:
    """Style presets, with enough detail for the UI to approximate them in CSS."""
    return [
        CaptionStyleOut(
            key=style.key,
            label=style.label,
            description=style.description,
            preview={
                "font": style.font,
                "primary": style.primary,
                "accent": style.accent,
                "outline": style.outline,
                "outlineWidth": style.outline_width,
                "allCaps": style.all_caps,
                "sizeRatio": style.size_ratio,
                "marginRatio": style.margin_v_ratio,
                "boxed": style.boxed,
                "animation": style.animation,
                "maxWords": style.max_words,
            },
        )
        for style in captions_module.PRESETS.values()
    ]


# --------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------


def _load_transcript(job_id: str) -> Transcript:
    workspace = JobWorkspace(job_id)
    if not workspace.transcript.exists():
        raise HTTPException(status_code=404, detail="This job has no transcript yet.")
    return Transcript.load(workspace.transcript)


def _crop_path_for(workspace: JobWorkspace, clip, source, ratio: str) -> CropPath:
    """Reuse the job's cached crop path, falling back to a centre crop.

    A re-export at a different ratio can't reuse a path computed for the
    original one, so the geometry is recomputed rather than stretched.
    """
    cached = workspace.crop_path(clip.id)
    if cached.exists() and ratio == "9:16":
        return CropPath.load(cached)

    aspect = {"9:16": (9, 16), "1:1": (1, 1), "16:9": (16, 9)}[ratio]
    return centre_crop(
        source.width or 1920,
        source.height or 1080,
        clip.end_s - clip.start_s,
        aspect_w=aspect[0],
        aspect_h=aspect[1],
    )
