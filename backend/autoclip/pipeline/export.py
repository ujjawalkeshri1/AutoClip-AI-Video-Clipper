"""Export stage — crop path plus captions plus audio normalisation to an MP4.

The whole render is a single ffmpeg pass. Per-shot crop segments are trimmed out
of the source, cropped independently, scaled to a common output size, and
concatenated in the filtergraph — which is what allows a wide two-shot and a
tight single to sit in one clip without a mid-stream frame-size change.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from .. import accel
from ..config import ExportSettings
from ..system import report
from . import captions as captions_module
from . import ffmpeg
from .captions import CaptionStyle
from .reframe.croppath import CropPath, segment_crop_filter
from .transcript import Word

log = logging.getLogger(__name__)

#: Output dimensions per aspect ratio. 1080-wide is the platform sweet spot:
#: high enough to avoid re-encode mush, low enough to upload quickly.
RATIOS: dict[str, tuple[int, int]] = {
    "9:16": (1080, 1920),
    "1:1": (1080, 1080),
    "16:9": (1920, 1080),
}

#: Loudness targets. -14 LUFS integrated with -1.5 dBTP is what every major
#: platform normalises toward, so hitting it ourselves avoids their processing.
LOUDNESS_TRUE_PEAK = -1.5
LOUDNESS_RANGE = 11.0

AUDIO_BITRATE = "192k"
AUDIO_SAMPLE_RATE = 48_000


class ExportError(RuntimeError):
    """Rendering a clip failed."""


@dataclass
class ExportRequest:
    source: Path
    destination: Path
    start_s: float
    end_s: float
    crop_path: CropPath
    words: list[Word]
    style: CaptionStyle
    ratio: str = "9:16"
    burn_captions: bool = True
    #: Optional short line burned in for the first seconds (e.g. "Watch what happens next").
    hook_text: str = ""
    #: Optional background music, looped to the clip length. None = original audio only.
    music: Path | None = None
    music_volume: float = 0.25
    game_volume: float = 1.0
    #: Duck the music under the original audio (sidechain compression).
    duck: bool = True
    #: False for muted recordings: the music then becomes the only audio.
    source_has_audio: bool = True

    @property
    def duration_s(self) -> float:
        return self.end_s - self.start_s


def ratio_dimensions(ratio: str) -> tuple[int, int]:
    if ratio not in RATIOS:
        raise ExportError(f"Unknown ratio {ratio!r}. Available: {', '.join(RATIOS)}")
    return RATIOS[ratio]


def slugify_title(title: str, *, max_length: int = 50) -> str:
    """Filename-safe slug for a clip title."""
    slug = re.sub(r"[^\w\s-]", "", title, flags=re.UNICODE).strip().lower()
    slug = re.sub(r"[\s_-]+", "-", slug).strip("-")
    return slug[:max_length] or "clip"


def output_filename(title: str, ratio: str) -> str:
    return f"{slugify_title(title)}_{ratio.replace(':', 'x')}.mp4"


# --------------------------------------------------------------------------
# Filtergraph construction
# --------------------------------------------------------------------------


def build_video_filtergraph(
    request: ExportRequest,
    *,
    subtitle_name: str | None,
    fonts_name: str = "fonts",
) -> str:
    """Build the ``-filter_complex`` video chain for one clip."""
    out_w, out_h = ratio_dimensions(request.ratio)
    segments = request.crop_path.segments
    if not segments:
        raise ExportError("The crop path has no segments.")

    parts: list[str] = []
    labels: list[str] = []

    for index, segment in enumerate(segments):
        label = f"v{index}"
        labels.append(f"[{label}]")

        if len(segments) == 1:
            # No trim needed; the input is already seeked to the clip.
            source_label = "[0:v]"
            prefix = ""
        else:
            source_label = f"[s{index}]"
            prefix = (
                f"[0:v]trim=start={segment.start_s:.4f}:end={segment.end_s:.4f},"
                f"setpts=PTS-STARTPTS{source_label}"
            )
            parts.append(prefix)

        if segment.fit:
            parts.extend(_fit_chain(source_label, index, out_w, out_h))
            continue

        chain = [segment_crop_filter(segment)]
        if segment.zoom > 0:
            chain.append(_zoom_filter(segment.zoom, out_w, out_h))
        chain.append(f"scale={out_w}:{out_h}:flags=lanczos")
        chain.append("setsar=1,format=yuv420p")

        parts.append(f"{source_label}{','.join(chain)}[{label}]")

    if len(segments) == 1:
        current = "[v0]"
    else:
        parts.append(f"{''.join(labels)}concat=n={len(segments)}:v=1:a=0[vcat]")
        current = "[vcat]"

    if request.burn_captions and subtitle_name is not None:
        # Bare relative names — ffmpeg runs with its cwd set to the render
        # workspace, so there is nothing here that needs escaping.
        parts.append(f"{current}ass=filename={subtitle_name}:fontsdir={fonts_name}[vout]")
    else:
        parts.append(f"{current}null[vout]")

    return ";".join(parts)


def _fit_chain(source_label: str, index: int, out_w: int, out_h: int) -> list[str]:
    """Fit the whole frame into the output over a blurred copy of itself.

    Used when the subjects are spread wider than any crop can hold. Plain black
    bars would be honest but look cheap on a phone; a blurred fill is what
    viewers are used to seeing and keeps the frame full-bleed.
    """
    return [
        f"{source_label}split=2[fitbg{index}][fitfg{index}]",
        (
            f"[fitbg{index}]scale={out_w}:{out_h}:force_original_aspect_ratio=increase,"
            f"crop={out_w}:{out_h},boxblur=24:2[bg{index}]"
        ),
        (
            f"[fitfg{index}]scale={out_w}:{out_h}:force_original_aspect_ratio=decrease:"
            f"flags=lanczos[fg{index}]"
        ),
        (f"[bg{index}][fg{index}]overlay=(W-w)/2:(H-h)/2,setsar=1,format=yuv420p[v{index}]"),
    ]


def _zoom_filter(zoom: float, out_w: int, out_h: int) -> str:
    """A slow push-in over the segment.

    Uses ``zoompan``, which is the only filter that can change apparent scale
    while holding a fixed output size. Off by default — at very small
    increments it can step visibly, and a locked frame beats a stuttering one.
    """
    end_zoom = 1.0 + zoom
    return f"zoompan=z='min(zoom+{zoom / 240:.6f},{end_zoom:.4f})':d=1:s={out_w}x{out_h}:fps=30"


def build_audio_filtergraph(settings: ExportSettings, game_volume: float = 1.0) -> str:
    """Simple (no music) audio chain: optional level change, then loudness normalisation."""
    norm = f"loudnorm=I={settings.loudness_lufs}:TP={LOUDNESS_TRUE_PEAK}:LRA={LOUDNESS_RANGE}"
    if abs(game_volume - 1.0) > 1e-3:
        return f"volume={game_volume:.3f},{norm}"
    return norm


def build_music_graph(request: ExportRequest, settings: ExportSettings) -> str:
    """Filter_complex fragment producing ``[aout]``: original audio + looped music.

    The music is trimmed to the clip, faded in/out, optionally ducked under the original
    audio, mixed with ``amix`` (normalize=0 keeps the levels you chose) and finally loudness
    normalised like every other export, so the result matches platform loudness.
    """
    d = max(0.1, request.duration_s)
    norm = f"loudnorm=I={settings.loudness_lufs}:TP={LOUDNESS_TRUE_PEAK}:LRA={LOUDNESS_RANGE}"
    fade_out_at = max(0.0, d - 1.0)
    music = (
        f"volume={request.music_volume:.3f},atrim=duration={d:.3f},asetpts=PTS-STARTPTS,"
        f"afade=t=in:d=0.4,afade=t=out:st={fade_out_at:.3f}:d=1.0"
    )
    if not request.source_has_audio:
        # Music is all there is; its level is set by the loudness normalisation.
        return f"[1:a]atrim=duration={d:.3f},asetpts=PTS-STARTPTS,afade=t=in:d=0.4,afade=t=out:st={fade_out_at:.3f}:d=1.0,{norm}[aout]"
    game = f"[0:a]volume={request.game_volume:.3f}"
    if request.duck:
        return (
            f"{game},asplit=2[g1][g2];[1:a]{music}[m0];"
            f"[m0][g2]sidechaincompress=threshold=0.03:ratio=8:attack=20:release=400[m1];"
            f"[g1][m1]amix=inputs=2:duration=first:dropout_transition=0:normalize=0,{norm}[aout]"
        )
    return (
        f"{game}[g1];[1:a]{music}[m1];"
        f"[g1][m1]amix=inputs=2:duration=first:dropout_transition=0:normalize=0,{norm}[aout]"
    )


def encoder_args(settings: ExportSettings, *, force_software: bool = False) -> list[str]:
    """Pick the video encoder and its quality settings.

    NVENC is materially faster where available. Its quality knob is ``-cq``
    rather than ``-crf``, and the two scales are close enough that reusing the
    configured value keeps output consistent between machines.
    """
    if settings.prefer_hardware_encoder and not force_software and report().ffmpeg.nvenc_works:
        return [
            "-c:v",
            "h264_nvenc",
            "-preset",
            "p5",
            "-rc",
            "vbr",
            "-cq",
            str(settings.crf),
            "-b:v",
            "0",
            "-profile:v",
            "high",
        ]
    if settings.prefer_hardware_encoder and not force_software and accel.probe_qsv()[1]:
        # Intel Quick Sync. Only reached when a real test encode succeeded, and
        # export_clip retries in software if a render still fails.
        return ["-c:v", "h264_qsv", "-preset", "medium", "-global_quality", str(settings.crf),
                "-profile:v", "high"]
    return [
        "-c:v",
        "libx264",
        "-preset",
        "medium",
        "-crf",
        str(settings.crf),
        "-profile:v",
        "high",
    ]


# --------------------------------------------------------------------------
# Rendering
# --------------------------------------------------------------------------


def _uses_hardware(settings: ExportSettings) -> bool:
    enc = encoder_args(settings)
    return any(x in enc for x in ("h264_nvenc", "h264_qsv"))


def _render_args(request: ExportRequest, filtergraph: str, settings: ExportSettings,
                 enc: list[str], pix_fmt: str) -> list[str]:
    """The ffmpeg argv for one render.

    Input and output paths are ordinary argv arguments, so they stay absolute and
    need no escaping regardless of the working directory.
    """
    args = [
        # Seeking before -i decodes from the nearest keyframe and is far faster
        # than an output-side seek; modern ffmpeg keeps it frame-accurate.
        "-ss", f"{request.start_s:.4f}",
        "-t", f"{request.duration_s:.4f}",
        "-i", str(request.source),
    ]
    if request.music is not None:
        # Loop the track as often as needed; build_music_graph trims it to the clip.
        args += ["-stream_loop", "-1", "-i", str(request.music)]
        args += ["-filter_complex", filtergraph + ";" + build_music_graph(request, settings),
                 "-map", "[vout]", "-map", "[aout]"]
    else:
        args += ["-filter_complex", filtergraph, "-map", "[vout]", "-map", "0:a?",
                 "-af", build_audio_filtergraph(settings, request.game_volume)]
    return args + [
        *enc,
        "-pix_fmt", pix_fmt,
        "-c:a", "aac",
        "-b:a", AUDIO_BITRATE,
        "-ar", str(AUDIO_SAMPLE_RATE),
        "-movflags", "+faststart",
        str(request.destination),
    ]


def export_clip(
    request: ExportRequest,
    *,
    work_dir: Path,
    settings: ExportSettings | None = None,
    on_progress: Callable[[float], None] | None = None,
    cancelled: Callable[[], bool] | None = None,
) -> Path:
    """Render one clip to an MP4 and return the output path.

    Preconditions:
        request.source exists; request.crop_path segments span the clip duration
        in clip-relative seconds.
    """
    settings = settings or ExportSettings()
    out_w, out_h = ratio_dimensions(request.ratio)

    # Each clip renders in its own workspace so concurrent exports can't collide
    # on the shared `captions.ass` name that the relative-path scheme requires.
    workspace = work_dir / request.destination.stem
    workspace.mkdir(parents=True, exist_ok=True)

    subtitle_name: str | None = None
    fonts_name = "fonts"
    render_cwd: Path | None = None

    if (request.burn_captions and request.words) or request.hook_text:
        ass_path = workspace / "captions.ass"
        if request.burn_captions and request.words:
            ass_path = captions_module.write_ass(
                ass_path,
                request.words,
                request.style,
                width=out_w,
                height=out_h,
                time_offset_s=request.start_s,
            )
        if request.hook_text:
            from ..gameplay.hook import add_hook

            add_hook(ass_path, request.hook_text, out_w, out_h)
        render_cwd, subtitle_name, fonts_name = ffmpeg.relative_filter_workspace(
            ass_path, captions_module.FONT_DIR
        )

    filtergraph = build_video_filtergraph(
        request, subtitle_name=subtitle_name, fonts_name=fonts_name
    )

    request.destination.parent.mkdir(parents=True, exist_ok=True)

    def render(software: bool) -> None:
        enc = encoder_args(settings, force_software=software)
        pix = "nv12" if "h264_qsv" in enc else "yuv420p"
        ffmpeg.run(
            _render_args(request, filtergraph, settings, enc, pix),
            total_duration_s=request.duration_s,
            on_progress=on_progress,
            cancelled=cancelled,
            cwd=render_cwd,
        )

    try:
        try:
            render(False)
        except ffmpeg.FFmpegError as exc:
            if not _uses_hardware(settings):
                raise
            # A hardware encoder that passed its probe can still fail on a real
            # filtergraph. Software encoding is slower but always works.
            log.warning("Hardware encode failed (%s); retrying with x264.", str(exc)[-200:])
            request.destination.unlink(missing_ok=True)
            render(True)
    except ffmpeg.FFmpegError as exc:
        raise ExportError(f"Could not render {request.destination.name}.\n{exc}") from exc

    if not request.destination.exists() or request.destination.stat().st_size == 0:
        raise ExportError(f"ffmpeg reported success but {request.destination.name} is empty.")

    if settings.write_srt and request.words:
        captions_module.write_srt(
            request.destination.with_suffix(".srt"),
            request.words,
            time_offset_s=request.start_s,
        )

    log.info(
        "Exported %s (%.1fs, %s)",
        request.destination.name,
        request.duration_s,
        request.ratio,
    )
    return request.destination
