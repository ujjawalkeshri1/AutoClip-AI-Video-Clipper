# Test and implementation report (revision 2)

**Read this first.** Everything below was built and tested in a **Linux sandbox** (1 CPU core, 3 GB RAM, Python 3.12,
no GPU, no browser, no Windows, no network except package indexes and GitHub). It was **not** run on your Windows
laptop, **not** run on real GTA 6 footage, and **no Whisper model could be downloaded**. Passing tests here do not
prove Windows compatibility and do not prove detection accuracy on real gameplay. Section 5 lists what you must verify.

## Revision 2: the installer failure you reported

**Symptom:** `install.ps1` said "Python 3.11/3.12 not found" although `python --version`, `py -3.11` and
`sys.executable` all worked.

**Root cause (reproduced, not guessed).** `Find-Python` ran this probe through a helper:
`python -c 'import sys; print("%d.%d|%s" % (...))'`. The code contains **double quotes**. Windows PowerShell 5.1 does
not escape embedded double quotes when launching a native program, so Python received
`print(%d.%d|%s % (...))`, a syntax error, exit code 1. Every candidate (`py -3.12`, `py -3.11`, `python`) failed the
same way and was silently skipped. My earlier test of this function ran under PowerShell 7 on Linux, which passes
arguments correctly, so it never showed. I reproduced the failure by running PowerShell 7 with
`$PSNativeCommandArgumentPassing = 'Legacy'` (its emulation of 5.1 argument passing): the old `Find-Python`
returned NOT FOUND, and Python's `argv` showed the quotes gone. When you answered Y to the winget prompt, winget
had nothing to install (Python 3.11 was already present), and the same broken detector then looked again and failed again.

**A second instance of the same bug:** the old post-install import check was also `python -c '...print("imports ok")'`
and would have failed right after detection was fixed. A static guard found both lines in the old script.

**Fix (all in `install.ps1`; nothing was suppressed or bypassed):**
* The probe has no double quotes, and imports `venv` and `ensurepip` too (a Python that cannot make a virtual environment is rejected).
* It prints `sys.version_info` and the real `sys.executable`; only 64-bit 3.11 or 3.12 is accepted; 3.11 is preferred over 3.12.
* Detection order: `-PythonPath` (new, optional) > `py -3.11` / `py -3.12` > every `python`/`python3` on PATH (not just the first) > the registry (PEP 514) > default install folders.
* The Microsoft Store `python.exe` alias in `WindowsApps` is recognised and **never executed** (running it can open the Store); it is skipped with an explanation, and used only if a real Store Python package is installed.
* If nothing is found it prints a report of every place tried, the command, and the reason, plus `where.exe python` and `py -0p`. `-DiagnosePython` prints the same report and changes nothing.
* Python is never reinstalled when a compatible one is found; the winget prompt appears only when none exists, and winget's "already installed" exit codes are no longer treated as failures.
* Dependency install shows live pip output, uses binary wheels only, skips work when requirements are unchanged, runs `pip check`, and ends with `scripts/verify_install.py` and a clear **INSTALLATION SUCCEEDED/FAILED** report.

**What this evidence does and does not show.** `tests/windows/test_python_detection.ps1` (20 checks, in both
argument-passing modes) shows the detection logic finds Python 3.11.9 in a path **containing a space**, ignores the Store alias
without executing it, still finds a real Python listed after the alias, rejects 3.13 and 32-bit, and honours `-PythonPath`.
It uses fake interpreters on Linux. It does **not** execute the Windows-only calls (registry, `Get-AppxPackage`, winget,
CIM), and it is an emulation of 5.1 argument passing, not 5.1 itself. **The authoritative check is you running
`.\install.ps1 -DiagnosePython` on your machine** (command below).

## Result summary

| | Count |
|---|---|
| Upstream AutoClip tests (logic unchanged; 3 assertions updated for the new Whisper default) | 276 passed |
| New tests for this project | 47 passed |
| **Total, default selection** | **323 passed, 0 failed** |
| Upstream slow/e2e tests (normally deselected) | 18 passed, 24 skipped (need a real talking video + Whisper model) |

Run with `pytest -m "not slow and not e2e and not golden"` (set `AUTOCLIP_PWSH` to a PowerShell 7 path to include the
installer behaviour tests; they skip when PowerShell is absent). New test files: `tests/test_gameplay.py`,
`tests/test_gameplay_render.py`, `tests/test_audio_mix.py`, `tests/test_windows_scripts.py`, `tests/windows/test_python_detection.ps1`;
the synthetic footage generator is `tests/synth.py`.

**Test footage:** `tests/synth.py` renders a 2-minute 640x360 clip with *known* events: gunfire burst (20-24 s),
explosion (40 s), police chase with lights and siren (70-78 s), a **silent** crash (95 s) and a shout (105 s),
plus a HUD overlay. It is synthetic. It proves the pipeline finds injected events; it says nothing about real games.

## 1. Tests completed successfully

Mapped to your list of 20 checks:

| # | Check | Evidence |
|---|---|---|
| 1 | Local video import | Upload through the API for MP4, MOV, MKV, WebM (tests) and AVI (ad hoc); corrupt file and wrong extension rejected cleanly (422/415); muted recordings accepted |
| 3 | Metadata extraction | Resolution, FPS, codecs, audio layout, file size, duration via `/api/sources/{id}/info` (test) and shown on the progress page |
| 4 | Audio analysis | Impacts, sirens, shouts, gunfire clicks separate from calm audio; a siren is not mistaken for a shout |
| 5 | Motion analysis | Flash/fire tones, red+blue lights, frame-difference during fast movement vs calm |
| 6 | Scene detection | Cheap histogram cut detector used for whole videos; PySceneDetect (upstream) remains for clip-level shots. Cut-snapping logic is exercised but the synthetic clip has no cuts, so cut detection itself has little coverage |
| 8 | Candidate generation | All injected events found with correct categories; **a silent crash and silent explosion/chase are found with the audio muted** (test `test_silent_visual_event_is_detected`); flat footage yields **no** candidates |
| 9 | AI scoring | 0-100, six independent factors, points sum to the score, weights change the result, honesty note present |
| 10 | Duplicate detection | No two clips cover the same moment; nearby distinct moments are kept apart |
| 11 | Clip boundaries | Duration limits (20-90 s); sentence snapping (starts at a sentence start, ends at a sentence end); edges at calm points |
| 12 | Vertical conversion | Exports verified with ffprobe: h264, 1080x1920, AAC; duration matches |
| 13 | Gameplay framing | HUD detected; `auto` picks whole-frame-over-blur when a crop would cut the HUD; motion crop stays in bounds; explicit modes honoured. Frames inspected by eye |
| 14, 15 | Subtitles and sync (renderer) | With known word timings, captions appear only while words are active (render-vs-render pixel comparison); no words means no captions |
| 16 | Metadata | Three titles, description, hashtags, hook, thumbnail time, explanation; `.json` and `.jpg` written beside each MP4 |
| 17 | Batch processing | Approve/reject/batch-generate through the API with progress; 2- and 4-clip batches rendered without errors |
| 18 | Exported MP4 playback | Decoded by ffprobe/ffmpeg (stream properties, frame extraction); also downloadable through the API |
| 19 | Application restart | A fresh app instance on the same data folder returns identical clips and approval state |
| 20 | Error handling | Corrupt/unsupported files; transcript mode on a muted video gives a clear 400; cancel mid-run; **pause genuinely stops progress** and resume completes; bad `config.yaml` ignored; invalid settings rejected (400) |
| R2 | Python detection | 20 behavioural checks under both argument-passing modes (see above); static guards: ASCII-only script, no PowerShell-7-only syntax, no double quotes in native-command arguments (they flag both bad lines in the old script) |
| R2 | Dependencies | `uv pip compile --only-binary :all:` for Windows x64 / CPython 3.11 resolves all 110 pins: every package has a prebuilt wheel, none needs a compiler. `pip check` reports no conflicts. The only overlap is `opencv-python` + `opencv-contrib-python` sharing the `cv2` folder (identical files, identical pinned version); `verify_install.py` fails if their versions ever differ |
| R2 | Install verification | `scripts/verify_install.py`: Python version, every critical import with functional micro-tests (OpenCV optical flow, librosa STFT/onset, numba JIT, soundfile, CTranslate2 compute types, MediaPipe tasks API), FFmpeg/ffprobe/libass/x264/QSV, folders, config, in-process app start. Passes here |
| R2 | Background audio | Rendered audio is decoded and analysed: a 3 s tone loops across a 12 s clip, output length equals clip length, music fades out, ducking lowers music during loud game audio, volume 0 removes original audio, muted recordings get music as their only audio, original audio stays audible under quiet music; upload/list/delete/validation/path-traversal and per-export overrides through the API |
| R2 | Presets | Low/Medium/High (`low_resource`/`medium`/`quality`, with `low`/`high` aliases): ordered by cost, applied through API and job overrides |
| R2 | Media details | `/api/sources/{id}/info` returns resolution, FPS, codecs, audio layout, file size; shown with a preview on the progress page |
| - | Hardware fallback | Quick Sync is selected only after a real test encode; a render that fails on a hardware encoder retries with x264 (exercised for real: this machine has no QSV, so the fallback ran) |
| - | Memory | Peak RSS 241 MB analysing 2 min and 244 MB analysing 10 min: flat, so the video is streamed, not loaded |
| - | Speed (1 slow core) | 1080p60: about 6.6x real time (about 9 minutes per hour of footage on this core). 640x360: tens of times real time. Your 8-core laptop should be faster; not measured |

## 2. Defects found by testing and fixed

Honest list of what was wrong at first (all fixed; no tests currently fail):

* Gunfire counter fired on calm audio (threshold relative to the loudest decile). Now relative to the block median.
* A siren was read as a shout (loud, voice-band, bright). Now suppressed by tonality.
* Overlapping windows of different events were merged into one clip. Now only one event seen twice is merged.
* The generic "Surprise" label outranked specific detections (shootout, explosion). Specific labels now win.
* A silent crash was invisible (nothing spikes at the stop). Added an abrupt-stop channel; labelled lower-confidence when there is no impact sound.
* Normalising to the 99th percentile let one explosion squash every moderate event. Switched to baseline plus fixed span.
* Gunfire clicks counted as a crash impact and muzzle flicker looked like movement. Impact is now discounted by gunfire; frame difference is brightness-normalised.
* **Muted screen recordings were rejected at import** (upstream required speech). Now accepted in gameplay mode (silent track synthesised); transcript mode refuses them with a clear message.
* **Upstream ignored a job's saved settings snapshot** (per-job overrides did nothing). The runner now uses it.
* The project `config.yaml` leaked into tests of built-in defaults. `AUTOCLIP_CONFIG_YAML` is now authoritative when set.
* The progress page showed no preview, FPS, audio info or file size. Added.
* **Revision 2:** the installer's Python probe and its import check both passed double-quoted code through a native command, which Windows PowerShell 5.1 corrupts (see top of this report). Fixed, with regression tests and a static guard.
* **Revision 2:** the old installer ran pip silently for minutes, reinstalled everything on every run and gave no summary of success or failure. It now streams pip output, skips unchanged work and ends with a clear result.
* **Revision 2:** `opencv-python` and `opencv-contrib-python` (pulled in by scenedetect and mediapipe) share one `cv2` folder. Safe at identical versions; now guarded by the verifier and documented.
* **Revision 2:** requested features that were missing: a Medium preset, background music mixing, an Output quality control in the UI (the CRF setting existed but was hidden). Added and tested.

## 3. Partially completed

* **YouTube URLs.** URL validation and the error path were tested. A real download was **not** (YouTube is blocked in the sandbox). Only authorised, downloadable videos work; ongoing livestreams are not assumed downloadable, so import a recording or VOD.
* **Whisper transcription and word timestamps.** Code is in place (windowed transcription, sentence snapping, captions), but **no model could be downloaded**, so real speech was never transcribed. The speech step was stubbed in engine/API tests; sentence snapping and the caption renderer were tested with hand-made words. Whisper's own accuracy and timing are untested.
* **Vision model.** Parsing, category/interest updates, the "skip when Ollama is missing" path and a mocked Ollama round-trip are tested. No real vision model was run.
* **Intel Quick Sync.** The negative probe and the software fallback are tested; the positive path (a real Intel GPU encode) is not.
* **Web interface.** Type-checked, production-built, served, and every API it uses is tested. **I could not run a browser here**, so I have not clicked through the pages (including the new Audio section, music selector, quality selector and Medium preset). Expect small layout or interaction bugs.
* **Performance.** Measured on a 1-core sandbox with synthetic video, not on your laptop.
* **Scene detection.** See table row 6.

## 4. Not tested at all

* Any real GTA 6 footage (including detection accuracy, false positives/negatives and the category rules on real audio).
* Hours-long videos; the 16 GB RAM budget with Whisper `small`; Quality mode end to end.
* NVENC; OpenVINO and NPU positive paths; the optional LLM title refinement with a real provider; speaker diarization (upstream feature).
* Livestream recordings from YouTube or local capture software specifically (any file works the same way).

## 5. Windows-specific tasks still unverified (please check)

* `install.ps1`: parsed with PowerShell 7's parser, scanned for PowerShell-7-only syntax, and its detection logic exercised against fake interpreters in an emulation of 5.1 argument passing. The Windows-only parts were **never executed**: CIM hardware queries, `winget`, the registry and Appx lookups, PATH refresh, execution-policy behaviour, `Start-Transcript`, `Get-FileHash`/`ConvertFrom-Json` stamp logic. A mistake in one of those would still stop the install, but the error will now be printed, not hidden.
* `launch.bat`: never executed (curl health check, `.env` parsing, duplicate-instance detection).
* `requirements.txt` resolves for Windows/3.11 with binary wheels, but was never installed on Windows.
* "Open containing folder" uses `explorer /select`. Caption fonts and FFmpeg filter paths with spaces or non-ASCII characters were exercised on Linux only. (Your user folder name contains a space; the installer and detection handle it in tests, but real Windows behaviour is unconfirmed.)
* Intel Arc 130V / Core Ultra: QSV, OpenVINO device listing and NPU detection. The `amix` `normalize=0` option used for background music needs FFmpeg 4.4 or newer (the winget Gyan build is much newer).

## Commands to run on your Windows machine (PowerShell 5.1)

```powershell
cd "C:\path\to\gta6-clipper"          # the folder where you unzipped it
powershell.exe -NoExit -ExecutionPolicy Bypass -File ".\install.ps1" -DiagnosePython
```
This changes nothing. Expect `RESULT: usable Python found: 3.11.9 at C:\Users\Ujjawal Keshri\AppData\Local\Programs\Python\Python311\python.exe (via py launcher (-3.11))`.
If it does not, send me the whole report it prints. Then:
```powershell
powershell.exe -NoExit -ExecutionPolicy Bypass -File ".\install.ps1"
.\.venv\Scripts\python.exe .\scripts\verify_install.py
.\launch.bat
```
Then import `examples\synthetic_test_video.mp4`, click Start, check four highlights appear, generate one, play it.
