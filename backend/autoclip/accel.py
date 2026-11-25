"""Intel acceleration probing: Quick Sync (FFmpeg QSV), OpenVINO devices, NPU.

Everything is *verified by trying*, never assumed from a device name:

* QSV is "available" only if a one-frame h264_qsv encode succeeds.
* OpenVINO devices are whatever the runtime itself reports. That a GPU or NPU is
  *listed* does not mean Whisper or any model here runs on it: faster-whisper
  (CTranslate2) uses CPU or CUDA only. These probes exist so ``autoclip doctor``
  can tell you honestly what the machine offers, and so QSV encoding can be used.
* Windows reports shared GPU memory as "AdapterRAM", often capped at ~2-4 GB by a
  32-bit field. It is not dedicated VRAM and is never treated as such.
"""

from __future__ import annotations

import functools
import json
import subprocess
import sys
from dataclasses import dataclass, field

from .pipeline import ffmpeg


@dataclass
class AccelReport:
    gpus: list[dict] = field(default_factory=list)
    intel_gpu: bool = False
    qsv_listed: bool = False
    qsv_works: bool = False
    qsv_error: str = ""
    openvino_installed: bool = False
    openvino_devices: list[str] = field(default_factory=list)
    npu_listed: bool = False
    notes: list[str] = field(default_factory=list)


def _run(args: list[str], timeout: float = 30.0) -> tuple[int, str]:
    try:
        r = subprocess.run(args, capture_output=True, text=True, timeout=timeout,
                           creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        return r.returncode, (r.stdout or "") + (r.stderr or "")
    except (OSError, subprocess.TimeoutExpired) as exc:
        return 1, str(exc)


def probe_gpus() -> list[dict]:
    if sys.platform != "win32":
        return []
    code, out = _run(["powershell", "-NoProfile", "-Command",
                      "Get-CimInstance Win32_VideoController | "
                      "Select-Object Name,AdapterRAM,DriverVersion | ConvertTo-Json"])
    if code != 0 or not out.strip():
        return []
    try:
        data = json.loads(out)
    except json.JSONDecodeError:
        return []
    return [data] if isinstance(data, dict) else list(data)


@functools.lru_cache(maxsize=1)
def probe_qsv() -> tuple[bool, bool, str]:
    """(listed, works, error). Works means a real one-frame encode succeeded."""
    exe = ffmpeg.ffmpeg_path()
    _, enc = _run([exe, "-hide_banner", "-encoders"])
    if "h264_qsv" not in enc:
        return False, False, "this FFmpeg build has no h264_qsv encoder"
    code, out = _run([exe, "-hide_banner", "-nostdin", "-f", "lavfi", "-i", "color=c=black:s=256x256:d=0.2",
                      "-c:v", "h264_qsv", "-pix_fmt", "nv12", "-frames:v", "1", "-f", "null", "-"])
    if code == 0:
        return True, True, ""
    line = next((l for l in out.splitlines() if "error" in l.lower() or "fail" in l.lower()), out[-200:])
    return True, False, line.strip()[:200]


def probe_openvino() -> tuple[bool, list[str]]:
    try:
        import openvino as ov
    except ImportError:
        return False, []
    try:
        return True, list(ov.Core().available_devices)
    except Exception:  # noqa: BLE001 - a broken runtime must not break doctor
        return True, []


@functools.lru_cache(maxsize=1)
def report() -> AccelReport:
    r = AccelReport()
    r.gpus = probe_gpus()
    r.intel_gpu = any("intel" in (g.get("Name") or "").lower() for g in r.gpus)
    r.qsv_listed, r.qsv_works, r.qsv_error = probe_qsv()
    r.openvino_installed, r.openvino_devices = probe_openvino()
    r.npu_listed = any(d.upper().startswith("NPU") for d in r.openvino_devices)
    if r.intel_gpu and not r.qsv_works:
        r.notes.append("Intel GPU found but Quick Sync encoding is not usable here; using CPU (x264). "
                       "Update the Intel graphics driver and use a full FFmpeg build to enable it.")
    if r.npu_listed:
        r.notes.append("An NPU is visible to OpenVINO, but the speech model here (faster-whisper) "
                       "does not run on it; transcription stays on CPU.")
    if not r.openvino_installed:
        r.notes.append("OpenVINO is not installed (optional; not needed for any built-in feature).")
    return r
