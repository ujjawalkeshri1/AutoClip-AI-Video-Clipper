"""Rendering-level checks: caption sync, hook text, encoder fallback, presets, config.yaml."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).parent))
from synth import make_gameplay_video  # noqa: E402

from autoclip import accel, config  # noqa: E402
from autoclip.config import ExportSettings, Settings  # noqa: E402
from autoclip.gameplay.presets import PRESETS, apply_preset  # noqa: E402
from autoclip.pipeline import captions, export  # noqa: E402
from autoclip.pipeline.reframe.croppath import CropPath, CropSegment, Strategy  # noqa: E402
from autoclip.pipeline.transcript import Word  # noqa: E402


@pytest.fixture(scope="module")
def video(tmp_path_factory) -> Path:
    return make_gameplay_video(tmp_path_factory.mktemp("rv") / "g.mp4", duration=40.0)


def _path() -> CropPath:
    seg = CropSegment(start_s=0.0, end_s=12.0, width=640, height=360, keyframes=[], strategy=Strategy.WIDE, fit=True)
    return CropPath(source_width=640, source_height=360, segments=[seg])


def _render(video, tmp_path, name, *, words=(), hook="", settings=None):
    req = export.ExportRequest(
        source=video, destination=tmp_path / f"{name}.mp4", start_s=10.0, end_s=22.0, crop_path=_path(),
        words=list(words), style=captions.get_style("bold_pop"), ratio="9:16", hook_text=hook)
    export.export_clip(req, work_dir=tmp_path / "w", settings=settings or ExportSettings(prefer_hardware_encoder=False))
    return req.destination


def _frame(mp4: Path, t: float) -> np.ndarray:
    raw = subprocess.run(["ffmpeg", "-v", "error", "-ss", f"{t}", "-i", str(mp4), "-frames:v", "1",
                          "-vf", "scale=216:384", "-f", "rawvideo", "-pix_fmt", "gray", "-"],
                         capture_output=True, check=True).stdout
    return np.frombuffer(raw, np.uint8).reshape(384, 216).astype(np.float32)


def _diff(a: Path, b: Path, t: float, rows: slice) -> float:
    return float(np.abs(_frame(a, t)[rows] - _frame(b, t)[rows]).mean())


class TestCaptions:
    def test_captions_appear_exactly_when_spoken(self, video, tmp_path):
        """Words are spoken 4.0-6.0 s into the clip. Renders with and without captions are
        compared: they may only differ while a word is on screen.

        This verifies the *renderer's* sync to the word timings it is given. It does not
        measure Whisper's own timing accuracy, which needs real speech and a downloaded model.
        """
        plain = _render(video, tmp_path, "plain")
        words = [Word("WATCH", 14.0, 14.7), Word("THIS", 14.7, 15.4), Word("EXPLOSION", 15.4, 16.0)]  # abs time
        capt = _render(video, tmp_path, "capt", words=words)
        lower = slice(int(384 * 0.45), int(384 * 0.85))      # lower-middle safe area
        before = _diff(plain, capt, 3.4, lower)
        during = _diff(plain, capt, 5.0, lower)
        after = _diff(plain, capt, 7.0, lower)
        assert during > 5 * max(before, after, 0.2), (before, during, after)

    def test_no_speech_means_no_fabricated_captions(self, video, tmp_path):
        a = _render(video, tmp_path, "a")
        b = _render(video, tmp_path, "b", words=[])
        assert _diff(a, b, 5.0, slice(0, 384)) < 0.5

    def test_hook_text_is_burned_in_top_area_early_only(self, video, tmp_path):
        plain = _render(video, tmp_path, "p2")
        hooked = _render(video, tmp_path, "h2", hook="Watch what happens next")
        top = slice(int(384 * 0.05), int(384 * 0.30))
        assert _diff(plain, hooked, 1.5, top) > 3.0       # visible during the first seconds
        assert _diff(plain, hooked, 8.0, top) < 1.0       # gone afterwards


class TestEncoderFallback:
    def test_failed_hardware_render_retries_in_software(self, video, tmp_path, monkeypatch):
        """Pretend Quick Sync passed its probe. On this machine it cannot actually encode,
        so the render must fail on h264_qsv and transparently succeed with x264."""
        monkeypatch.setattr(accel, "probe_qsv", lambda: (True, True, ""))
        enc = export.encoder_args(ExportSettings(prefer_hardware_encoder=True))
        if "h264_qsv" not in enc:
            pytest.skip("NVENC works on this machine, so QSV is not selected")
        out = _render(video, tmp_path, "qsv", settings=ExportSettings(prefer_hardware_encoder=True))
        probe = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v", "-show_entries", "stream=codec_name,width,height",
                                "-of", "csv=p=0", str(out)], capture_output=True, text=True).stdout.strip()
        assert probe == "h264,1080,1920"

    def test_hardware_off_never_selects_qsv(self, monkeypatch):
        monkeypatch.setattr(accel, "probe_qsv", lambda: (True, True, ""))
        assert "libx264" in export.encoder_args(ExportSettings(prefer_hardware_encoder=False))

    def test_qsv_not_selected_when_probe_fails(self, monkeypatch):
        monkeypatch.setattr(accel, "probe_qsv", lambda: (True, False, "no device"))
        assert "h264_qsv" not in export.encoder_args(ExportSettings(prefer_hardware_encoder=True))


class TestConfig:
    def test_presets(self):
        s = Settings()
        apply_preset(s, "quality")
        assert (s.whisper.model, s.gameplay.sample_fps, s.gameplay.transcribe_scope) == ("small", 4.0, "full")
        apply_preset(s, "medium")
        assert (s.whisper.model, s.gameplay.sample_fps, s.gameplay.transcribe_scope) == ("small", 2.0, "candidates")
        apply_preset(s, "low_resource")
        assert (s.whisper.model, s.gameplay.sample_fps, s.gameplay.transcribe_scope) == ("base", 1.0, "candidates")
        apply_preset(s, "high")                       # alias for quality
        assert s.gameplay.preset == "quality"
        apply_preset(s, "low")                        # alias for low_resource
        assert s.gameplay.preset == "low_resource"
        with pytest.raises(ValueError):
            apply_preset(s, "turbo")
        assert set(PRESETS) == {"low_resource", "medium", "quality"}

    def test_presets_are_ordered_by_cost(self):
        low, med, high = (PRESETS[k] for k in ("low_resource", "medium", "quality"))
        assert low["sample_fps"] < med["sample_fps"] < high["sample_fps"]
        assert low["max_candidates"] <= med["max_candidates"] <= high["max_candidates"]

    def test_defaults_are_low_resource_cpu_friendly(self):
        s = Settings()
        assert s.gameplay.preset == "low_resource" and s.whisper.model == "base" and not s.gameplay.vision.enabled

    def test_config_yaml_provides_defaults_and_json_overrides(self, tmp_path, monkeypatch):
        y = tmp_path / "c.yaml"
        y.write_text("gameplay:\n  max_candidates: 7\n  framing: blur_fit\nwhisper:\n  model: tiny\n")
        monkeypatch.setenv("AUTOCLIP_CONFIG_YAML", str(y))
        s = config.load()
        assert (s.gameplay.max_candidates, s.gameplay.framing, s.whisper.model) == (7, "blur_fit", "tiny")
        s.whisper.model = "small"
        config.save(s)
        again = config.load()
        assert again.whisper.model == "small" and again.gameplay.max_candidates == 7

    def test_bad_yaml_does_not_block_startup(self, tmp_path, monkeypatch):
        y = tmp_path / "bad.yaml"
        y.write_text("gameplay: [unclosed")
        monkeypatch.setenv("AUTOCLIP_CONFIG_YAML", str(y))
        assert config.load().analysis_mode == "gameplay"

    def test_shipped_config_yaml_is_valid(self):
        import yaml

        data = yaml.safe_load((Path(__file__).parents[1] / "config.yaml").read_text())
        s = Settings.model_validate(data)
        assert s.gameplay.weights["entertainment"] == 25 and s.export.ratio == "9:16"


class TestAccel:
    def test_report_never_raises_and_is_honest(self):
        accel.report.cache_clear()
        r = accel.report()
        assert isinstance(r.qsv_works, bool) and isinstance(r.openvino_devices, list)
        if not r.qsv_works:
            assert r.qsv_error
