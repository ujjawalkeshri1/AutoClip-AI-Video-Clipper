"""Background music + original audio mixing, verified by analysing the rendered audio."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import numpy as np
import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).parent))
from synth import make_gameplay_video  # noqa: E402

from autoclip import app as app_module  # noqa: E402
from autoclip.app import create_app  # noqa: E402
from autoclip.config import ExportSettings  # noqa: E402
from autoclip.pipeline import captions, export  # noqa: E402
from autoclip.pipeline.reframe.croppath import CropPath, CropSegment, Strategy  # noqa: E402

TONE_HZ = 440.0
SR = 16000


@pytest.fixture(scope="module")
def video(tmp_path_factory) -> Path:
    return make_gameplay_video(tmp_path_factory.mktemp("am") / "g.mp4", duration=40.0)


@pytest.fixture(scope="module")
def muted(video, tmp_path_factory) -> Path:
    out = tmp_path_factory.mktemp("amm") / "muted.mp4"
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-i", str(video), "-an", "-c:v", "copy", str(out)], check=True)
    return out


@pytest.fixture(scope="module")
def tone3s(tmp_path_factory) -> Path:
    """A 3-second 440 Hz tone: shorter than the 12 s clip, so it must loop."""
    p = tmp_path_factory.mktemp("tone") / "tone.wav"
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i", f"sine=frequency={TONE_HZ}:duration=3",
                    "-ar", "44100", "-ac", "2", str(p)], check=True)
    return p


def _path() -> CropPath:
    seg = CropSegment(start_s=0.0, end_s=12.0, width=640, height=360, keyframes=[], strategy=Strategy.WIDE, fit=True)
    return CropPath(source_width=640, source_height=360, segments=[seg])


def render(video, tmp_path, name, *, music=None, music_volume=0.25, game_volume=1.0, duck=True, has_audio=True):
    req = export.ExportRequest(
        source=video, destination=tmp_path / f"{name}.mp4", start_s=10.0, end_s=22.0, crop_path=_path(),
        words=[], style=captions.get_style("bold_pop"), music=music, music_volume=music_volume,
        game_volume=game_volume, duck=duck, source_has_audio=has_audio)
    export.export_clip(req, work_dir=tmp_path / "w", settings=ExportSettings(prefer_hardware_encoder=False))
    return req.destination


def audio_of(mp4: Path) -> np.ndarray:
    raw = subprocess.run(["ffmpeg", "-v", "error", "-i", str(mp4), "-vn", "-ac", "1", "-ar", str(SR), "-f", "f32le", "-"],
                         capture_output=True, check=True).stdout
    return np.frombuffer(raw, np.float32)


def tone_fraction(y: np.ndarray, t0: float, t1: float) -> float:
    """Share of the window's energy that sits within +-15 Hz of the music tone."""
    seg = y[int(t0 * SR):int(t1 * SR)]
    spec = np.abs(np.fft.rfft(seg * np.hanning(len(seg)))) ** 2
    f = np.fft.rfftfreq(len(seg), 1 / SR)
    band = (f > TONE_HZ - 15) & (f < TONE_HZ + 15)
    return float(spec[band].sum() / (spec.sum() + 1e-12))


def tone_level(y: np.ndarray, t0: float, t1: float) -> float:
    seg = y[int(t0 * SR):int(t1 * SR)]
    spec = np.abs(np.fft.rfft(seg * np.hanning(len(seg)))) / len(seg)
    f = np.fft.rfftfreq(len(seg), 1 / SR)
    return float(spec[(f > TONE_HZ - 15) & (f < TONE_HZ + 15)].max())


class TestMix:
    def test_no_music_has_no_tone(self, video, tmp_path):
        y = audio_of(render(video, tmp_path, "plain"))
        assert tone_fraction(y, 1.0, 9.0) < 0.05

    def test_music_is_audible_and_loops_past_its_own_length(self, video, tone3s, tmp_path):
        out = render(video, tmp_path, "m", music=tone3s)
        y = audio_of(out)
        assert tone_fraction(y, 1.0, 2.5) > 0.3                      # first pass
        assert tone_fraction(y, 6.0, 9.0) > 0.3, "the 3 s track did not loop to cover the 12 s clip"

    def test_output_is_exactly_clip_length_not_music_length(self, video, tone3s, tmp_path):
        out = render(video, tmp_path, "len", music=tone3s)
        dur = float(subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(out)],
                                   capture_output=True, text=True).stdout)
        assert abs(dur - 12.0) < 0.3, dur

    def test_music_fades_out_at_the_end(self, video, tone3s, tmp_path):
        y = audio_of(render(video, tmp_path, "fade", music=tone3s, duck=False))
        assert tone_level(y, 10.9, 11.9) < 0.6 * tone_level(y, 4.0, 5.0)

    def test_game_volume_zero_leaves_only_music(self, video, tone3s, tmp_path):
        y = audio_of(render(video, tmp_path, "gv0", music=tone3s, game_volume=0.0, duck=False))
        assert tone_fraction(y, 1.0, 9.0) > 0.9

    def test_original_audio_is_kept_underneath(self, video, tone3s, tmp_path):
        """Gunfire clicks sit at clip time 10-12 s. With a quiet music bed they must still be there."""
        y = audio_of(render(video, tmp_path, "keep", music=tone3s, music_volume=0.05, duck=False))
        assert tone_fraction(y, 10.0, 12.0) < tone_fraction(y, 1.0, 9.0), "original audio vanished under the music"

    def test_ducking_lowers_music_while_the_game_is_loud(self, video, tone3s, tmp_path):
        loud_win, calm_win = (10.2, 11.8), (3.0, 7.0)            # gunfire vs calm ambience
        ducked = audio_of(render(video, tmp_path, "d1", music=tone3s, duck=True))
        flat = audio_of(render(video, tmp_path, "d0", music=tone3s, duck=False))
        r_duck = tone_level(ducked, *loud_win) / tone_level(ducked, *calm_win)
        r_flat = tone_level(flat, *loud_win) / tone_level(flat, *calm_win)
        assert r_duck < 0.85 * r_flat, (r_duck, r_flat)

    def test_muted_recording_gets_music_as_its_only_audio(self, muted, tone3s, tmp_path):
        out = render(muted, tmp_path, "mm", music=tone3s, has_audio=False)
        streams = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "stream=codec_type", "-of", "csv=p=0", str(out)],
                                 capture_output=True, text=True).stdout.split()
        assert "audio" in streams
        assert tone_fraction(audio_of(out), 1.0, 9.0) > 0.9

    def test_muted_recording_without_music_stays_silent(self, muted, tmp_path):
        out = render(muted, tmp_path, "ms", has_audio=False)
        streams = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "stream=codec_type", "-of", "csv=p=0", str(out)],
                                 capture_output=True, text=True).stdout.split()
        assert "audio" not in streams

    def test_game_volume_changes_the_original_level(self, video, tmp_path):
        """With loudnorm on top, compare relative dynamics instead: volume=0 must silence the file."""
        quiet = audio_of(render(video, tmp_path, "g0", game_volume=0.0))
        assert float(np.abs(quiet).max()) < 0.01


# -- API ---------------------------------------------------------------------


@pytest.fixture
def client(autoclip_home, monkeypatch):
    monkeypatch.delenv(app_module.ENV_NO_WORKER, raising=False)
    with TestClient(create_app()) as c:
        yield c


class TestMusicApi:
    def _upload(self, client, path: Path, name: str | None = None):
        with open(path, "rb") as f:
            return client.post("/api/music", files={"file": (name or path.name, f, "audio/wav")})

    def test_upload_list_preview_delete(self, client, tone3s):
        r = self._upload(client, tone3s, "my song.wav")
        assert r.status_code == 201, r.text
        t = r.json()
        assert t["name"] == "my song.wav" and 2.5 < t["duration_s"] < 3.5
        assert [x["id"] for x in client.get("/api/music").json()] == [t["id"]]
        assert client.get(f"/api/music/{t['id']}/file").status_code == 200
        assert client.delete(f"/api/music/{t['id']}").status_code == 204
        assert client.get("/api/music").json() == []

    def test_rejects_bad_uploads(self, client, tmp_path):
        txt = tmp_path / "a.txt"; txt.write_text("hello")
        assert self._upload(client, txt).status_code == 415
        fake = tmp_path / "fake.mp3"; fake.write_text("this is not audio")
        r = self._upload(client, fake)
        assert r.status_code == 422 and "could not be read" in r.json()["detail"]
        assert client.get("/api/music").json() == [], "a rejected upload must leave nothing behind"

    def test_path_traversal_ids_are_rejected(self, client):
        for bad in ("..%2F..%2Fetc%2Fpasswd", "../x", "ABCDEF123456", "short"):
            assert client.get(f"/api/music/{bad}/file").status_code in (404, 422)
            assert client.delete(f"/api/music/{bad}").status_code in (404, 405, 422)

    def test_default_track_setting_is_validated_and_cleared_on_delete(self, client, tone3s):
        tid = self._upload(client, tone3s).json()["id"]
        assert client.put("/api/settings", json={"audio": {"music_id": "nope"}}).status_code == 400
        assert client.put("/api/settings", json={"audio": {"music_volume": 3}}).status_code == 400
        ok = client.put("/api/settings", json={"audio": {"music_id": tid, "music_volume": 0.4}})
        assert ok.status_code == 200 and ok.json()["audio"]["music_id"] == tid
        client.delete(f"/api/music/{tid}")
        assert client.get("/api/settings").json()["audio"]["music_id"] == ""

    def test_export_through_the_api_mixes_music(self, client, video, tone3s):
        with open(video, "rb") as f:
            sid = client.post("/api/sources/upload", files={"file": ("g.mp4", f, "video/mp4")}).json()["id"]
        import time

        from autoclip.gameplay import speech
        from autoclip.pipeline.transcript import Transcript

        speech.transcribe_windows = lambda *a, **k: Transcript()
        jid = client.post("/api/jobs", json={"source_id": sid, "settings": {"analysis_mode": "gameplay"}}).json()["id"]
        end = time.time() + 120
        while time.time() < end and client.get(f"/api/jobs/{jid}").json()["status"] not in ("done", "failed"):
            time.sleep(1)
        cid = client.get(f"/api/jobs/{jid}/clips").json()[0]["id"]
        tid = self._upload(client, tone3s).json()["id"]

        # Re-exporting a clip replaces its file, so analyse the first render before making the second.
        plain = client.post(f"/api/clips/{cid}/export", json={"music_id": "none"}).json()
        pe = next(e for e in client.get("/api/exports").json() if e["id"] == plain["id"])
        yp = audio_of(Path(pe["path"]))
        mixed = client.post(f"/api/clips/{cid}/export", json={"music_id": tid, "music_volume": 0.5}).json()
        me = next(e for e in client.get("/api/exports").json() if e["id"] == mixed["id"])
        ym = audio_of(Path(me["path"]))
        mid = len(yp) / SR / 2
        assert tone_fraction(yp, mid - 1, mid + 1) < 0.2 < tone_fraction(ym, mid - 1, mid + 1)

        # a deleted/unknown track is a clear error, not a crash
        r = client.post(f"/api/clips/{cid}/export", json={"music_id": "0123456789ab"})
        assert r.status_code == 400 and "no longer exists" in r.json()["detail"]
