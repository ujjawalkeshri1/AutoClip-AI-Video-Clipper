"""Optional opening hook text, burned in as an ASS event."""

from __future__ import annotations

from pathlib import Path

import pysubs2


def add_hook(ass_path: Path, text: str, width: int, height: int, *, start_s: float = 0.2,
             end_s: float = 3.2) -> Path:
    """Add a hook line near the top safe area. Creates the file if it does not exist."""
    subs = pysubs2.load(str(ass_path)) if ass_path.exists() else pysubs2.SSAFile()
    subs.info["PlayResX"], subs.info["PlayResY"] = str(width), str(height)
    size = int(height * 0.045)
    subs.styles["Hook"] = pysubs2.SSAStyle(
        fontname="Inter", fontsize=size, bold=True, alignment=8,
        primarycolor=pysubs2.Color(255, 255, 255), outlinecolor=pysubs2.Color(0, 0, 0),
        backcolor=pysubs2.Color(0, 0, 0, 120), outline=max(3, size // 9), shadow=2,
        marginv=int(height * 0.12), marginl=int(width * 0.08), marginr=int(width * 0.08))
    subs.append(pysubs2.SSAEvent(start=int(start_s * 1000), end=int(end_s * 1000),
                                 text=text.replace("\n", "\\N"), style="Hook"))
    ass_path.parent.mkdir(parents=True, exist_ok=True)
    subs.save(str(ass_path))
    return ass_path
