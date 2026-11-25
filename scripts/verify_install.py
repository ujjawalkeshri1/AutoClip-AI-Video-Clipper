"""Post-install verification. Run:  .venv\\Scripts\\python.exe scripts\\verify_install.py

Checks the Python version, every critical import (with a tiny functional test where an
import alone proves little), MediaPipe, FFmpeg/ffprobe (libass, x264, optional Quick Sync),
folders, configuration, and that the app itself can start. Exit code 0 means every
*critical* check passed. WARN lines are optional extras and never fail the run.

Nothing here downloads models or touches your data: it uses a throwaway data folder.
"""

from __future__ import annotations

import importlib
import os
import platform
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FAILED: list[str] = []
WARNED: list[str] = []


def line(status: str, name: str, detail: str = "") -> None:
    print(f"  [{status:<4}] {name}" + (f": {detail}" if detail else ""))
    if status == "FAIL":
        FAILED.append(name)
    elif status == "WARN":
        WARNED.append(name)


def run(args: list[str], timeout: int = 60) -> tuple[int, str]:
    try:
        r = subprocess.run(args, capture_output=True, text=True, timeout=timeout,
                           creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        return r.returncode, (r.stdout or "") + (r.stderr or "")
    except (OSError, subprocess.TimeoutExpired) as exc:
        return 1, str(exc)


def version_of(mod) -> str:
    v = getattr(mod, "__version__", None) or getattr(mod, "version", None)
    if v is not None and not isinstance(v, str):  # e.g. yt_dlp.version is a submodule
        v = getattr(v, "__version__", None)
    return v if isinstance(v, str) else ""


def check_python() -> None:
    v = sys.version_info
    ok = (v.major, v.minor) in ((3, 11), (3, 12))
    line("OK" if ok else "FAIL", "Python",
         f"{v.major}.{v.minor}.{v.micro} {'64' if sys.maxsize > 2**32 else '32'}-bit at {sys.executable}"
         + ("" if ok else " (only 3.11 and 3.12 are supported)"))
    if sys.maxsize <= 2**32:
        line("FAIL", "Python 64-bit", "MediaPipe and CTranslate2 need 64-bit Python")
    in_venv = sys.prefix != getattr(sys, "base_prefix", sys.prefix)
    line("OK" if in_venv else "WARN", "Virtual environment", sys.prefix if in_venv else "not running inside .venv")


def check_import(name: str, extra=None, critical: bool = True, note: str = "") -> None:
    try:
        mod = importlib.import_module(name)
    except Exception as exc:  # noqa: BLE001 - report anything that stops an import
        line("FAIL" if critical else "WARN", f"import {name}", f"{exc.__class__.__name__}: {exc}")
        return
    try:
        detail = (version_of(mod) + (" " + note if note else "")).strip()
        if extra:
            detail = (detail + " " + (extra(mod) or "")).strip()
        line("OK", f"import {name}", detail)
    except Exception as exc:  # noqa: BLE001
        line("FAIL" if critical else "WARN", f"import {name} (functional test)", f"{exc.__class__.__name__}: {exc}")


def t_cv2(cv2) -> str:
    import numpy as np

    a = (np.random.rand(90, 160) * 255).astype("uint8")
    flow = cv2.calcOpticalFlowFarneback(a, np.roll(a, 2, axis=1), None, 0.5, 3, 15, 3, 5, 1.2, 0)
    assert flow.shape == (90, 160, 2)
    return "optical flow ok"


def t_librosa(librosa) -> str:
    import numpy as np

    y = np.sin(2 * np.pi * 440 * np.arange(16000) / 16000).astype("float32")
    s = librosa.stft(y, n_fft=1024, hop_length=256)
    librosa.onset.onset_strength(S=librosa.amplitude_to_db(abs(s)))
    return "stft/onset ok"


def t_soundfile(sf) -> str:
    import numpy as np

    with tempfile.TemporaryDirectory() as d:
        p = Path(d) / "t.wav"
        sf.write(str(p), np.zeros(1600, dtype="float32"), 16000)
        assert sf.info(str(p)).frames == 1600
    return "wav read/write ok"


def t_mediapipe(mp) -> str:
    # The same imports the app's reframe code uses (pipeline/reframe/faces.py).
    from mediapipe.tasks.python import BaseOptions  # noqa: F401
    from mediapipe.tasks.python.vision import FaceLandmarker, FaceLandmarkerOptions, RunningMode  # noqa: F401

    return "tasks API ok"


def t_numba(numba) -> str:
    @numba.njit(cache=False)
    def f(x):
        return x + 1

    assert f(1) == 2
    return "JIT ok"


def t_ctranslate2(ct) -> str:
    return f"cpu types: {', '.join(sorted(ct.get_supported_compute_types('cpu')))}"


def check_imports() -> None:
    check_import("numpy")
    check_import("scipy")
    check_import("cv2", t_cv2)
    check_import("numba", t_numba)
    check_import("librosa", t_librosa)
    check_import("soundfile", t_soundfile)
    check_import("ctranslate2", t_ctranslate2)
    check_import("faster_whisper")
    check_import("scenedetect")
    check_import("mediapipe", t_mediapipe)
    check_import("pysubs2")
    check_import("yaml")
    check_import("yt_dlp")
    check_import("httpx")
    check_import("pydantic")
    check_import("fastapi")
    check_import("uvicorn")
    check_import("openvino", critical=False, note="(optional: lists Intel GPU/NPU devices)")
    check_import("autoclip")
    check_opencv_duplicates()


def check_opencv_duplicates() -> None:
    """scenedetect needs opencv-python and mediapipe needs opencv-contrib-python. Both install
    the same `cv2` folder, which is harmless only while their versions are identical."""
    from importlib import metadata

    found = {}
    for name in ("opencv-python", "opencv-contrib-python", "opencv-python-headless", "opencv-contrib-python-headless"):
        try:
            found[name] = metadata.version(name)
        except metadata.PackageNotFoundError:
            pass
    if len(found) <= 1:
        line("OK", "OpenCV distributions", ", ".join(f"{k} {v}" for k, v in found.items()) or "none")
    elif len(set(found.values())) == 1:
        line("OK", "OpenCV distributions", "two packages share the cv2 folder at the same version "
             f"({next(iter(found.values()))}): fine. Do not upgrade or uninstall one on its own")
    else:
        line("FAIL", "OpenCV distributions", "different versions are installed side by side (" +
             ", ".join(f"{k} {v}" for k, v in found.items()) + "). Fix: pip uninstall -y opencv-python "
             "opencv-contrib-python, then pip install -r requirements.txt")


def check_ffmpeg() -> None:
    ff, fp = shutil.which("ffmpeg"), shutil.which("ffprobe")
    if not ff or not fp:
        line("FAIL", "FFmpeg/ffprobe on PATH", "install with: winget install Gyan.FFmpeg   (then open a NEW terminal)")
        return
    code, out = run([ff, "-hide_banner", "-version"])
    line("OK" if code == 0 else "FAIL", "FFmpeg", (out.splitlines() or [""])[0] + f"  [{ff}]")
    line("OK" if run([fp, "-version"])[0] == 0 else "FAIL", "ffprobe", fp)
    _, filters = run([ff, "-hide_banner", "-filters"])
    has_ass = any(len(p) > 1 and p[1] == "ass" for p in (l.split() for l in filters.splitlines()))
    line("OK" if has_ass else "FAIL", "FFmpeg libass (caption burn-in)",
         "available" if has_ass else "missing: install the full build (Gyan.FFmpeg)")
    _, enc = run([ff, "-hide_banner", "-encoders"])
    line("OK" if "libx264" in enc else "FAIL", "FFmpeg libx264 (CPU H.264)", "available" if "libx264" in enc else "missing")
    if "h264_qsv" in enc:
        code, _ = run([ff, "-hide_banner", "-nostdin", "-f", "lavfi", "-i", "color=c=black:s=256x256:d=0.2",
                       "-c:v", "h264_qsv", "-pix_fmt", "nv12", "-frames:v", "1", "-f", "null", "-"])
        line("OK", "Intel Quick Sync", "test encode succeeded; exports will use it" if code == 0
             else "listed but the test encode failed: exports use the CPU (this is fine)")
    else:
        line("OK", "Intel Quick Sync", "not in this FFmpeg build: exports use the CPU (this is fine)")


def check_project() -> None:
    home = Path(os.environ.get("AUTOCLIP_HOME") or Path.home() / ".autoclip")
    try:
        home.mkdir(parents=True, exist_ok=True)
        probe = home / ".write_test"
        probe.write_text("ok")
        probe.unlink()
        line("OK", "Data folder writable", str(home))
    except OSError as exc:
        line("FAIL", "Data folder writable", f"{home}: {exc}")
    ui = ROOT / "backend" / "autoclip" / "static" / "index.html"
    line("OK" if ui.exists() else "WARN", "Web interface bundle",
         "present" if ui.exists() else "missing: run  cd frontend; npm ci; npm run build")
    cfg = ROOT / "config.yaml"
    try:
        import yaml
        from autoclip.config import Settings

        Settings.model_validate(yaml.safe_load(cfg.read_text(encoding="utf-8")) or {})
        line("OK", "config.yaml", "valid")
    except Exception as exc:  # noqa: BLE001
        line("FAIL", "config.yaml", f"{exc.__class__.__name__}: {str(exc)[:160]}")
    fonts = ROOT / "backend" / "autoclip" / "assets" / "fonts"
    if fonts.exists():
        n = len(list(fonts.glob("*.[to]tf")))
        line("OK" if n else "WARN", "Caption fonts", f"{n} bundled")
    for f in ("install.ps1", "launch.bat", "requirements.txt"):
        line("OK" if (ROOT / f).exists() else "FAIL", f"file {f}", "")


def check_app_starts() -> None:
    old = {k: os.environ.get(k) for k in ("AUTOCLIP_HOME", "AUTOCLIP_NO_WORKER")}
    with tempfile.TemporaryDirectory() as d:
        os.environ["AUTOCLIP_HOME"] = d
        os.environ["AUTOCLIP_NO_WORKER"] = "1"
        try:
            from fastapi.testclient import TestClient

            from autoclip.app import create_app

            with TestClient(create_app()) as c:
                r = c.get("/api/health")
                s = c.get("/api/settings").json()
                line("OK" if r.status_code == 200 else "FAIL", "App starts (in-process)",
                     f"/api/health {r.status_code}; analysis mode {s.get('analysis_mode')}, Whisper {s['whisper']['model']}")
        except Exception as exc:  # noqa: BLE001
            line("FAIL", "App starts (in-process)", f"{exc.__class__.__name__}: {str(exc)[:200]}")
        finally:
            for k, v in old.items():
                if v is None:
                    os.environ.pop(k, None)
                else:
                    os.environ[k] = v
            try:  # release the SQLite file before the temp folder is removed (Windows)
                from autoclip import db

                db.reset_connections()
            except Exception:  # noqa: BLE001
                pass


def main() -> int:
    print(f"Verifying install on {platform.platform()}")
    print("Python")
    check_python()
    print("Libraries")
    check_imports()
    print("FFmpeg")
    check_ffmpeg()
    print("Project")
    check_project()
    check_app_starts()
    print()
    if FAILED:
        print(f"RESULT: FAIL ({len(FAILED)} critical problem(s): {', '.join(FAILED)})")
        return 1
    print(f"RESULT: PASS ({len(WARNED)} optional warning(s))")
    return 0


if __name__ == "__main__":
    sys.exit(main())
