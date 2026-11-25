"""Guards for the Windows installer and launcher.

These cannot run Windows-only code (registry, winget, CIM). They do catch the bug class that blocked
a real install: Windows PowerShell 5.1 strips double quotes from native-command arguments, which broke the
Python probe in install.ps1 and made an installed Python 3.11 look missing.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
INSTALL = ROOT / "install.ps1"
PWSH = os.environ.get("AUTOCLIP_PWSH") or shutil.which("pwsh")


def test_install_script_is_plain_ascii():
    """Windows PowerShell 5.1 reads a BOM-less script as ANSI, so any non-ASCII byte can corrupt it."""
    bad = [i for i, c in enumerate(INSTALL.read_bytes()) if c > 127]
    assert not bad, f"{len(bad)} non-ASCII bytes in install.ps1"
    assert all(c < 128 for c in (ROOT / "launch.bat").read_bytes())


def test_no_powershell7_only_syntax():
    text = "\n".join(l for l in INSTALL.read_text().splitlines() if not l.strip().startswith("#"))
    for pat in (r"\?\?", r"\?\.", r"&&", r"\|\|", r"-AsHashtable", r"ForEach-Object -Parallel", r"\bif\s*\(.*\)\s*\?"):
        assert not re.search(pat, text), f"PowerShell 7-only syntax {pat!r} would fail on Windows PowerShell 5.1"


def test_native_calls_never_pass_double_quotes():
    """5.1 strips embedded double quotes: arguments to native programs must use single quotes."""
    offenders = [l.strip() for l in INSTALL.read_text().splitlines()
                 if re.search(r"Invoke-Native(Live)?\b", l) and '"' in l and not l.strip().startswith(("function", "#"))]
    assert not offenders, offenders


def test_python_probe_has_no_double_quotes_and_is_valid_python():
    m = re.search(r'\$script:PyProbeCode = "(.*)"\s*$', INSTALL.read_text(), re.M)
    assert m, "probe not found"
    code = m.group(1)
    assert '"' not in code
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True)
    assert out.returncode == 0 and re.match(r"^3\.\d+\.\d+\|1\|", out.stdout), out.stderr


def test_launch_bat_has_no_unescaped_parentheses_in_echo_blocks():
    """An unescaped ')' inside an echo within a parenthesised block ends the block early."""
    depth, bad = 0, []
    for n, l in enumerate((ROOT / "launch.bat").read_text().splitlines(), 1):
        s = l.strip()
        if s.lower().startswith("echo") and depth > 0 and re.search(r"(?<!\^)[()]", s[4:]):
            bad.append(n)
        depth += s.count("(") - s.count(")") if not s.lower().startswith(("echo", "rem")) else 0
    assert not bad, f"launch.bat lines {bad}"


def test_requirements_cover_the_critical_packages():
    reqs = (ROOT / "requirements.txt").read_text().lower()
    for pkg in ("mediapipe==", "opencv-contrib-python==", "faster-whisper==", "librosa==", "ctranslate2==",
                "soundfile==", "scipy==", "pyyaml==", "scenedetect=="):
        assert pkg in reqs, pkg
    assert "torch" not in reqs and "nvidia" not in reqs, "CUDA/PyTorch must not be required"
    ver = {m.group(1): m.group(2) for m in re.finditer(r"^(opencv-[a-z-]+)==([\d.]+)", reqs, re.M)}
    assert len(set(ver.values())) == 1, f"mismatched OpenCV pins: {ver}"


@pytest.mark.skipif(not PWSH, reason="PowerShell (pwsh) is not installed")
def test_install_script_parses():
    code = ("$e=$null;$t=$null;[void][System.Management.Automation.Language.Parser]::ParseFile("
            f"'{INSTALL}',[ref]$t,[ref]$e); if($e.Count){{$e|%{{$_.Message}}; exit 1}}")
    r = subprocess.run([PWSH, "-NoProfile", "-Command", code], capture_output=True, text=True)
    assert r.returncode == 0, r.stdout + r.stderr


@pytest.mark.skipif(not PWSH or sys.platform == "win32", reason="needs pwsh on a POSIX host (uses fake interpreters)")
@pytest.mark.parametrize("mode", ["Standard", "Legacy"])
def test_python_detection_behaviour(mode):
    """Legacy emulates Windows PowerShell 5.1 argument passing: this is the regression test for the reported bug."""
    r = subprocess.run([PWSH, "-NoProfile", "-File", str(ROOT / "tests/windows/test_python_detection.ps1"), "-Mode", mode],
                       capture_output=True, text=True, timeout=300)
    assert r.returncode == 0, r.stdout + r.stderr
    assert "ALL PASSED" in r.stdout


def test_verify_install_script_passes_in_this_environment():
    r = subprocess.run([sys.executable, str(ROOT / "scripts/verify_install.py")], capture_output=True, text=True, timeout=300)
    assert r.returncode == 0, r.stdout + r.stderr
    assert "RESULT: PASS" in r.stdout
