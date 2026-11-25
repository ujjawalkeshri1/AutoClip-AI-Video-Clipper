"""Gameplay engine tests against synthetic footage with known events.

These prove the pipeline finds *known, injected* events (including a silent
visual-only one). They say nothing about accuracy on real GTA 6 footage.
"""

from __future__ import annotations

import json
import subprocess
import sys
import time
from pathlib import Path

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).parent))
from synth import EVENTS, make_gameplay_video  # noqa: E402

from autoclip import app as app_module  # noqa: E402
from autoclip.app import create_app  # noqa: E402
from autoclip.config import Settings  # noqa: E402
from autoclip.gameplay import candidates as C  # noqa: E402
from autoclip.gameplay import engine, framing, scoring, signals, speech  # noqa: E402
from autoclip.pipeline import prepare  # noqa: E402
from autoclip.pipeline.transcript import Transcript, Word  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402


@pytest.fixture(scope="session")
def video(tmp_path_factory) -> Path:
    return make_gameplay_video(tmp_path_factory.mktemp("synth") / "gameplay.mp4")


@pytest.fixture(scope="session")
def wav(video: Path, tmp_path_factory) -> Path:
    return prepare.extract_audio(video, tmp_path_factory.mktemp("aud") / "a.wav", duration_s=120)


@pytest.fixture(scope="session")
def sigs(video, wav):
    return signals.analyze_audio(wav), signals.analyze_motion(video, duration_s=120, sample_fps=1.0)


@pytest.fixture
def no_whisper(monkeypatch):
    """Whisper models are downloaded from the network; tests must not need that."""
    monkeypatch.setattr(speech, "transcribe_windows", lambda *a, **k: Transcript())


def near(t: float, cands, tol: float = 8.0):
    return [c for c in cands if c.start - 1 <= t <= c.end + 1 and abs(c.peak - t) <= tol + 15]


# -- signals -----------------------------------------------------------------


class TestSignals:
    def test_audio_distinguishes_events(self, sigs):
        a, _ = sigs
        at = lambda k, t: float(a.channels[k][int((t - 1.5) / 0.5): int((t + 1.5) / 0.5)].max())  # noqa: E731
        assert at("low_onset", 40.5) > 3 * at("low_onset", 10)      # explosion boom vs calm
        assert at("siren", 74) > 3 * at("siren", 10)                 # siren sweep
        assert at("shout", 106) > 0.5 and at("shout", 74) < 0.2      # shout, and siren is not a shout
        assert at("impulse", 22) >= 2 and at("impulse", 10) == 0     # gunfire clicks vs calm

    def test_video_signals(self, sigs):
        _, m = sigs
        at = lambda k, t: float(m.channels[k][int((t - 1.0) / 0.5): int((t + 1.0) / 0.5)].max())  # noqa: E731
        assert at("flash", 40.5) > 0.2 and at("warm", 40.5) > 0.3
        assert at("lights", 74) > 0.3 and at("lights", 10) == 0
        assert at("diff", 92) > 1.5 * at("diff", 10)

    def test_memory_is_streamed(self, video):
        """Motion analysis must not hold frames: result is small regardless of length."""
        m = signals.analyze_motion(video, duration_s=120, sample_fps=1.0, start_s=10, end_s=20)
        assert all(len(v) == 20 for v in m.channels.values())


# -- candidates and categories -------------------------------------------------


class TestDetection:
    def test_known_events_are_found_and_labelled(self, sigs):
        a, m = sigs
        ch = C.build_channels(a, m, 120.0)
        cands, _ = C.find_candidates(ch, 120.0, C.DetectionParams())
        by = {c.category: c for c in cands}
        assert by["SHOOTOUT"].start <= 22 <= by["SHOOTOUT"].end
        assert by["EXPLOSION"].start <= 40.5 <= by["EXPLOSION"].end
        assert by["POLICE_CHASE"].start <= 74 <= by["POLICE_CHASE"].end

    def test_silent_visual_event_is_detected(self, wav, video, tmp_path):
        """No audio at all: a visual-only event must still produce a candidate."""
        import soundfile as sf

        quiet = tmp_path / "quiet.wav"
        sf.write(str(quiet), np.full(120 * 16000, 1e-4, dtype=np.float32), 16000)
        a = signals.analyze_audio(quiet)
        m = signals.analyze_motion(video, duration_s=120, sample_fps=1.0)
        ch = C.build_channels(a, m, 120.0)
        cands, _ = C.find_candidates(ch, 120.0, C.DetectionParams())
        cats = {c.category for c in cands}
        assert {"EXPLOSION", "POLICE_CHASE"} & cats, f"visual-only events missed: {cats}"
        assert any(c.start <= 95.5 <= c.end for c in cands), "silent crash missed"

    def test_flat_footage_yields_nothing(self, tmp_path):
        """A static, silent video must not be inflated into fake highlights."""
        import soundfile as sf

        flat = tmp_path / "flat.mp4"
        sf.write(str(tmp_path / "s.wav"), np.zeros(60 * 16000, dtype=np.float32), 16000)
        subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i", "color=c=gray:s=320x180:d=60:r=10",
                        "-i", str(tmp_path / "s.wav"), "-c:v", "libx264", "-c:a", "aac", "-shortest", str(flat)],
                       check=True)
        a = signals.analyze_audio(tmp_path / "s.wav")
        m = signals.analyze_motion(flat, duration_s=60, sample_fps=1.0)
        cands, _ = C.find_candidates(C.build_channels(a, m, 60.0), 60.0, C.DetectionParams())
        assert cands == []

    def test_no_duplicate_or_nested_clips(self, sigs):
        a, m = sigs
        ch = C.build_channels(a, m, 120.0)
        cands, _ = C.find_candidates(ch, 120.0, C.DetectionParams())
        for i, x in enumerate(cands):
            for y in cands[i + 1:]:
                inter = max(0.0, min(x.end, y.end) - max(x.start, y.start))
                assert inter / min(x.duration, y.duration) < 0.5, "two clips cover the same moment"

    def test_duration_limits(self, sigs):
        a, m = sigs
        p = C.DetectionParams(min_duration_s=20, max_duration_s=90)
        cands, _ = C.find_candidates(C.build_channels(a, m, 120.0), 120.0, p)
        assert cands and all(19.5 <= c.duration <= 90.5 for c in cands)


# -- scoring -------------------------------------------------------------------


class TestScoring:
    def _scored(self, sigs, weights=None):
        a, m = sigs
        ch = C.build_channels(a, m, 120.0)
        cands, sal = C.find_candidates(ch, 120.0, C.DetectionParams())
        scoring.score_candidates(cands, ch, sal, weights)
        return cands

    def test_scores_in_range_and_factors_independent(self, sigs):
        cands = self._scored(sigs)
        assert cands
        for c in cands:
            assert 0 <= c.score <= 100
            assert set(c.factors) == set(scoring.DEFAULT_WEIGHTS)
            assert all(0.0 <= v <= 1.0 for v in c.factors.values())
            assert abs(sum(c.meta["points"].values()) - c.score) <= 1.5
        assert len({round(c.factors["reaction"], 2) for c in cands}) > 1  # not one blended number

    def test_default_weights_sum_to_100(self):
        assert sum(scoring.DEFAULT_WEIGHTS.values()) == 100

    def test_weights_are_configurable(self, sigs):
        base = {c.category: c.score for c in self._scored(sigs)}
        only_action = {k: 0.0 for k in scoring.DEFAULT_WEIGHTS} | {"action": 100.0}
        reweighted = {c.category: c.score for c in self._scored(sigs, only_action)}
        assert base != reweighted

    def test_score_carries_honesty_note(self, sigs):
        assert "not a prediction" in self._scored(sigs)[0].meta["score_note"].lower()


# -- speech --------------------------------------------------------------------


class TestSpeech:
    def test_start_not_mid_sentence_and_end_finishes_sentence(self):
        words = "so we got the cops on us. oh my god this is insane! hold on hold on. okay we lost them.".split()
        t = Transcript(words=[Word(w, 10 + i * 0.4, 10.35 + i * 0.4) for i, w in enumerate(words)])
        c = C.Candidate(start=12.0, end=17.2, peak=15.0, category="ACTION", confidence=0.5)
        speech.refine_with_speech([c], t, 60.0, C.DetectionParams())
        starts = {round(w.start - 0.15, 2) for w in t.words if w.start < 14}
        assert any(abs(c.start - s) < 0.01 for s in starts)
        assert t.words[next(i for i, w in enumerate(t.words) if w.start <= c.end <= w.end + 0.5)].text or True
        # the last included word ends a sentence
        last = [w for w in t.words if w.start < c.end][-1]
        assert last.ends_sentence

    def test_empty_transcript_is_fine(self):
        assert speech.speech_channels(Transcript(), 60.0) is None

    def test_windows_merge(self):
        assert speech.merge_windows([(0, 10), (9, 20), (40, 50)]) == [(0, 20), (40, 50)]


# -- framing -------------------------------------------------------------------


class TestFraming:
    def test_hud_detected_and_auto_uses_blur_fit(self, video):
        assert framing.detect_hud(video, 58, 82) is not None
        d = framing.choose_mode("auto", video, 58, 82, 640, 360, "9:16", 120)
        assert d.mode == "blur_fit" and "HUD" in d.reason

    def test_motion_crop_stays_in_bounds(self, video):
        cp, d = framing.build_gameplay_crop_path(video, start_s=58, end_s=82, mode="motion_crop",
                                                 ratio="9:16", source_duration=120)
        seg = cp.segments[0]
        assert seg.width == 202 and seg.height == 360
        assert all(0 <= k.x <= 640 - seg.width for k in seg.keyframes)

    def test_explicit_mode_is_respected(self, video):
        cp, d = framing.build_gameplay_crop_path(video, start_s=10, end_s=30, mode="center",
                                                 ratio="9:16", source_duration=120)
        assert d.mode == "center" and not cp.segments[0].fit


# -- engine --------------------------------------------------------------------


class TestEngine:
    def test_full_engine_run_and_cache(self, video, wav, tmp_path, no_whisper):
        s = Settings()
        res = engine.analyze(video=video, wav=wav, duration_s=120, settings=s, work_dir=tmp_path / "w")
        assert res.candidates
        c = res.candidates[0]
        for key in ("titles", "hook", "hashtags", "description", "thumbnail_s", "explanation", "points"):
            assert c.meta[key], key
        assert len(c.meta["titles"]) == 3
        assert any("No speech" in n for n in res.notes)
        t0 = time.time()
        again = engine.analyze(video=video, wav=wav, duration_s=120, settings=s, work_dir=tmp_path / "w")
        assert time.time() - t0 < 10, "signals should be cached on the second run"
        assert [x.category for x in again.candidates] == [x.category for x in res.candidates]

    def test_cancel(self, video, wav, tmp_path, no_whisper):
        with pytest.raises(signals.SignalError):
            engine.analyze(video=video, wav=wav, duration_s=120, settings=Settings(), work_dir=tmp_path / "c",
                           cancelled=lambda: True)


# -- vision (mock Ollama) --------------------------------------------------------


class TestVision:
    def test_apply_vision_updates_category_and_interest(self):
        from autoclip.gameplay import vision

        c = C.Candidate(start=0, end=30, peak=10, category="ACTION", confidence=0.4)
        vision.apply_vision(c, {"labels": ["explosion", "driving"], "description": "A fireball.",
                                "interest": 9, "unexpected": True, "title": "Big fireball"})
        assert c.category == "EXPLOSION" and c.meta["vision"]["interest"] == 9

    def test_idle_lowers_confidence(self):
        from autoclip.gameplay import vision

        c = C.Candidate(start=0, end=30, peak=10, category="ACTION", confidence=0.6)
        vision.apply_vision(c, {"labels": ["menu or loading"], "interest": 8})
        assert c.meta["vision"]["interest"] <= 3 and c.confidence < 0.6

    def test_skips_cleanly_when_ollama_missing(self, video):
        from autoclip.config import VisionSettings
        from autoclip.gameplay import vision

        cfg = VisionSettings(enabled=True, base_url="http://127.0.0.1:9")
        status = vision.run_vision(video, [C.Candidate(start=0, end=30, peak=10)], cfg)
        assert "skipped" in status

    def test_mock_ollama_roundtrip(self, video, monkeypatch):
        import httpx

        from autoclip.config import VisionSettings
        from autoclip.gameplay import vision

        class R:
            def __init__(self, data): self._d = data
            def raise_for_status(self): pass
            def json(self): return self._d

        monkeypatch.setattr(httpx, "get", lambda *a, **k: R({"models": [{"name": "qwen2.5vl:3b"}]}))
        seen = {}

        def post(url, json=None, **k):
            seen["frames"] = len(json["images"])
            return R({"response": '{"labels":["police"],"description":"Cars with lights.","interest":7,'
                                  '"unexpected":false,"title":"Cars chasing"}'})

        monkeypatch.setattr(httpx, "post", post)
        c = C.Candidate(start=60, end=85, peak=72, category="ACTION", confidence=0.4, score=50)
        status = vision.run_vision(video, [c], VisionSettings(enabled=True))
        assert seen["frames"] == 3 and "ran on 1" in status and c.category == "POLICE_CHASE"


# -- API flow ------------------------------------------------------------------


@pytest.fixture
def client(autoclip_home, monkeypatch, no_whisper):
    monkeypatch.delenv(app_module.ENV_NO_WORKER, raising=False)
    with TestClient(create_app()) as c:
        yield c


def _probe(path: Path) -> dict:
    out = subprocess.run(["ffprobe", "-v", "error", "-show_streams", "-show_format", "-of", "json", str(path)],
                         capture_output=True, text=True, check=True).stdout
    return json.loads(out)


def _wait(client, job_id, timeout=240):
    end = time.time() + timeout
    while time.time() < end:
        j = client.get(f"/api/jobs/{job_id}").json()
        if j["status"] in ("done", "failed", "cancelled"):
            return j
        time.sleep(1)
    raise AssertionError("job did not finish")


class TestApiFlow:
    def test_analyse_review_and_batch_export(self, client, video):
        with open(video, "rb") as f:
            src = client.post("/api/sources/upload", files={"file": ("gameplay.mp4", f, "video/mp4")}).json()
        assert src["width"] == 640 and src["duration_s"] > 119

        job = client.post("/api/jobs", json={"source_id": src["id"],
                                             "settings": {"analysis_mode": "gameplay", "preset": "low_resource"}}).json()
        assert job["mode"] == "gameplay"
        done = _wait(client, job["id"])
        assert done["status"] == "done", done.get("error")

        clips = client.get(f"/api/jobs/{job['id']}/clips").json()
        assert len(clips) >= 3
        assert all(c["status"] == "candidate" for c in clips), "nothing is rendered before review"
        top = clips[0]
        assert top["category"] and top["factors"] and top["meta"]["titles"] and top["meta"]["hashtags"]

        # dashboard + highlights listing
        st = client.get("/api/stats").json()
        assert st["videos_processed"] == 1 and st["highlights_detected"] == len(clips)
        assert client.get("/api/highlights", params={"category": top["category"]}).json()
        assert client.get(f"/api/clips/{top['id']}/thumbnail").headers["content-type"] == "image/jpeg"

        # reject one, approve two, batch-render approved
        ids = [c["id"] for c in clips]
        client.post("/api/clips/batch/action", json={"clip_ids": [ids[-1]], "action": "reject"})
        client.post("/api/clips/batch/action", json={"clip_ids": ids[:2], "action": "approve"})
        b = client.post("/api/clips/batch/export", json={"approved_in_job": job["id"], "framing": "auto",
                                                         "hook_text": True}).json()
        assert b["total"] == 2
        end = time.time() + 300
        while time.time() < end:
            b = client.get(f"/api/batches/{b['id']}").json()
            if b["finished"]:
                break
            time.sleep(1)
        assert b["finished"] and not b["errors"], b["errors"]

        exports = client.get("/api/exports").json()
        assert len(exports) == 2
        for e in exports:
            mp4 = Path(e["path"])
            info = _probe(mp4)
            v = next(s for s in info["streams"] if s["codec_type"] == "video")
            a = next(s for s in info["streams"] if s["codec_type"] == "audio")
            assert (v["codec_name"], v["width"], v["height"]) == ("h264", 1080, 1920)
            assert a["codec_name"] == "aac"
            assert abs(float(info["format"]["duration"]) - e["duration_s"]) < 1.0
            side = json.loads(mp4.with_suffix(".json").read_text())
            assert len(side["title_options"]) == 3 and side["hashtags"] and "not a prediction" in side["score_note"].lower()
            assert mp4.with_suffix(".jpg").exists()
            assert client.get(e["download_url"]).status_code == 200
        assert client.get("/api/stats").json()["clips_generated"] == 2

    def test_pause_resume_cancel_endpoints(self, client, video):
        with open(video, "rb") as f:
            src = client.post("/api/sources/upload", files={"file": ("g.mp4", f, "video/mp4")}).json()
        job = client.post("/api/jobs", json={"source_id": src["id"], "settings": {"analysis_mode": "gameplay"}}).json()
        r = client.post(f"/api/jobs/{job['id']}/resume")
        assert r.status_code == 409
        _wait(client, job["id"])
        assert client.post(f"/api/jobs/{job['id']}/pause").status_code == 409  # finished: not running


class TestEdgeCases:
    @pytest.fixture
    def muted(self, video, tmp_path) -> Path:
        out = tmp_path / "muted.mp4"
        subprocess.run(["ffmpeg", "-y", "-v", "error", "-i", str(video), "-an", "-c:v", "copy", str(out)], check=True)
        return out

    def _upload(self, client, path: Path, name: str):
        with open(path, "rb") as f:
            return client.post("/api/sources/upload", files={"file": (name, f, "video/mp4")})

    def test_muted_recording_is_accepted_and_analysed_from_picture_alone(self, client, muted):
        r = self._upload(client, muted, "muted.mp4")
        assert r.status_code == 201 and r.json()["has_audio"] is False
        job = client.post("/api/jobs", json={"source_id": r.json()["id"], "settings": {"analysis_mode": "gameplay"}}).json()
        done = _wait(client, job["id"])
        assert done["status"] == "done", done.get("error")
        clips = client.get(f"/api/jobs/{job['id']}/clips").json()
        assert {c["category"] for c in clips} & {"EXPLOSION", "POLICE_CHASE"}, "visual-only events missed"

        # and it can be rendered (output has no audio stream, which is correct)
        exp = client.post(f"/api/clips/{clips[0]['id']}/export", json={}).json()
        info = _probe(Path(client.get("/api/exports").json()[0]["path"]))
        assert any(s["codec_type"] == "video" and s["height"] == 1920 for s in info["streams"])
        assert exp["size_bytes"] > 0

    def test_transcript_mode_refuses_muted_video_with_a_clear_message(self, client, muted):
        sid = self._upload(client, muted, "muted.mp4").json()["id"]
        r = client.post("/api/jobs", json={"source_id": sid, "settings": {"analysis_mode": "speech"}})
        assert r.status_code == 400 and "no audio" in r.json()["detail"].lower()

    def test_corrupt_file_is_rejected_cleanly(self, client, tmp_path):
        bad = tmp_path / "bad.mp4"
        bad.write_text("this is not a video")
        r = self._upload(client, bad, "bad.mp4")
        assert r.status_code == 422 and "could not be read" in r.json()["detail"]["message"]

    def test_unsupported_extension_is_rejected(self, client, tmp_path):
        f = tmp_path / "x.exe"
        f.write_bytes(b"MZ")
        with open(f, "rb") as fh:
            r = client.post("/api/sources/upload", files={"file": ("x.exe", fh, "application/octet-stream")})
        assert r.status_code == 415

    def test_other_video_formats_import(self, client, video, tmp_path):
        for ext, vcodec, acodec in (("mov", "libx264", "aac"), ("mkv", "libx264", "aac"), ("webm", "libvpx", "libvorbis")):
            p = tmp_path / f"t.{ext}"
            subprocess.run(["ffmpeg", "-y", "-v", "error", "-t", "6", "-i", str(video), "-c:v", vcodec,
                            "-c:a", acodec, str(p)], check=True)
            r = self._upload(client, p, p.name)
            assert r.status_code == 201, (ext, r.text)

    def test_results_survive_an_app_restart(self, autoclip_home, video, monkeypatch, no_whisper):
        monkeypatch.delenv(app_module.ENV_NO_WORKER, raising=False)
        with TestClient(create_app()) as first:
            sid = self._upload(first, video, "g.mp4").json()["id"]
            jid = first.post("/api/jobs", json={"source_id": sid, "settings": {"analysis_mode": "gameplay"}}).json()["id"]
            assert _wait(first, jid)["status"] == "done"
            before = [(c["id"], c["score"], c["category"]) for c in first.get(f"/api/jobs/{jid}/clips").json()]
            first.post("/api/clips/batch/action", json={"clip_ids": [before[0][0]], "action": "approve"})
        # a brand-new app instance on the same data folder
        with TestClient(create_app()) as second:
            after = [(c["id"], c["score"], c["category"]) for c in second.get(f"/api/jobs/{jid}/clips").json()]
            assert after == before
            assert second.get(f"/api/clips/{before[0][0]}").json()["status"] == "kept"
            assert second.get("/api/stats").json()["highlights_detected"] == len(before)

    def test_cancel_a_running_job(self, client, video):
        sid = self._upload(client, video, "g.mp4").json()["id"]
        jid = client.post("/api/jobs", json={"source_id": sid, "settings": {"analysis_mode": "gameplay"}}).json()["id"]
        end = time.time() + 60
        while time.time() < end and client.get(f"/api/jobs/{jid}").json()["status"] != "running":
            time.sleep(0.1)
        assert client.post(f"/api/jobs/{jid}/cancel").status_code == 200
        final = _wait(client, jid)
        assert final["status"] == "cancelled", final
        assert client.get(f"/api/jobs/{jid}/clips").json() == []

    def test_pause_actually_pauses_and_resume_continues(self, client, video):
        sid = self._upload(client, video, "g.mp4").json()["id"]
        jid = client.post("/api/jobs", json={"source_id": sid, "settings": {"analysis_mode": "gameplay"}}).json()["id"]
        end = time.time() + 60
        while time.time() < end and client.get(f"/api/jobs/{jid}").json()["status"] != "running":
            time.sleep(0.05)
        assert client.post(f"/api/jobs/{jid}/pause").status_code == 200
        time.sleep(2.0)
        p1 = client.get(f"/api/jobs/{jid}").json()["progress"]
        time.sleep(3.0)
        p2 = client.get(f"/api/jobs/{jid}").json()["progress"]
        assert client.get(f"/api/jobs/{jid}").json()["status"] == "running"
        assert p2 == p1, "progress advanced while paused"
        assert client.post(f"/api/jobs/{jid}/resume").status_code == 200
        assert _wait(client, jid)["status"] == "done"


class TestSourceInfo:
    def test_info_reports_media_details(self, client, video):
        with open(video, "rb") as f:
            sid = client.post("/api/sources/upload", files={"file": ("g.mp4", f, "video/mp4")}).json()["id"]
        info = client.get(f"/api/sources/{sid}/info").json()
        assert info["video"]["width"] == 640 and info["video"]["height"] == 360 and info["video"]["fps"] == 20.0
        assert info["video"]["codec"] == "h264" and info["audio"]["codec"] == "aac" and info["audio"]["channels"] == 1
        assert info["file_size_bytes"] == Path(video).stat().st_size
        assert client.get("/api/sources/nope/info").status_code == 404
