"""REST API routers."""

from __future__ import annotations

from fastapi import APIRouter

from . import clips, gameplay, jobs, music, settings, sources

__all__ = ["api_router"]

api_router = APIRouter()
api_router.include_router(sources.router)
api_router.include_router(jobs.router)
# gameplay first: its /clips/batch/* paths must win over /clips/{clip_id}/*.
api_router.include_router(gameplay.router)
api_router.include_router(clips.router)
api_router.include_router(settings.router)
api_router.include_router(music.router)
