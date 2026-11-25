# AutoClip — AI Gameplay Video Clipper

**Long gameplay video in. Short, vertical highlight clips out.**

AutoClip is a local-first Windows application that analyses gameplay recordings, finds potentially interesting moments, lets you review them, and creates vertical MP4 clips with captions, titles and thumbnails.

It is designed to run on your own computer. Your source videos, project database and exported clips stay on your computer unless you choose to share them.

> **Important:** This project is a gameplay-focused extension of the MIT-licensed AutoClip project by Jad Ghazi. The original project documentation is preserved in [`docs/UPSTREAM_README.md`](docs/UPSTREAM_README.md), and the existing MIT licence notice is retained. See [`LICENSE`](LICENSE).
>
> Only download or process videos you own or have permission to use. AutoClip does not make copyrighted footage free to reuse, bypass DRM, sign into accounts, or publish clips automatically.

## What AutoClip does

1. Import a local gameplay video or a supported YouTube video/VOD URL.
2. Analyse audio, motion, colour changes and (optionally) speech/visual information.
3. Find possible highlights and give each one a score and explanation.
4. Review, approve or reject the suggested moments.
5. Generate vertical clips with captions and metadata.
6. Export MP4 clips that you can upload yourself.

The app includes a browser-based dashboard, but the processing happens locally on your computer.

## Download and install (Windows beginners)

**You do not need to know programming, use Git, or open VS Code.**

1. Open the repository's GitHub page.
2. Click the green **Code** button, then choose **Download ZIP**.
3. When the ZIP finishes downloading, right-click it and choose **Extract All**.
4. Open the extracted `autoclip` folder.
5. Find `install.ps1`. Right-click it and choose **Run with PowerShell**.
6. Read the installer messages. If Windows asks whether to install a missing required program, read the prompt and choose **Y** if you want the installer to install it.
7. Wait for the final message. Continue only when you see **INSTALLATION SUCCEEDED**.
8. Double-click `launch.bat`. Keep the black window open while you use AutoClip.
9. Your browser should open the app at `http://localhost:8765`.

If Windows blocks the installer, open PowerShell in the `autoclip` folder and run:

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File ".\install.ps1"
```

This is a one-time launch option for this command; it does not permanently change your computer's PowerShell security setting.

For screenshots-style, beginner-friendly steps and explanations, read **[INSTALLATION_GUIDE.md](INSTALLATION_GUIDE.md)**. If something goes wrong, see **[TROUBLESHOOTING.md](TROUBLESHOOTING.md)**.

## What your computer needs

| Requirement | Details |
|---|---|
| Operating system | Windows 10 or Windows 11, 64-bit |
| Python | Python 3.11 or 3.12, 64-bit |
| FFmpeg | Full FFmpeg build with `ffmpeg`, `ffprobe`, `libass` and `libx264` |
| Disk space | Allow at least 3 GB for installation and model downloads; more for your videos and exports |
| Memory | 8 GB can work with Low resource mode; 16 GB is more comfortable |
| Internet | Needed during installation and the first model download; local processing works after required files are downloaded |

The installer checks for a suitable Python version and FFmpeg. If Python is missing, it can offer to install Python 3.11 using Windows Package Manager (`winget`). The installer may also offer to install the full FFmpeg build. It creates a private `.venv` folder inside the project and does not require administrator rights for the normal setup.

**Python 3.13 and newer are not supported by this package at present.** MediaPipe does not provide the required compatible wheels for those versions. Use 64-bit Python 3.11 or 3.12.

The frontend is already built and included in this repository. A normal Windows user does **not** need Node.js to install or run the app.

## First time using the app

1. Start AutoClip by double-clicking `launch.bat`.
2. Open **Import**.
3. Choose a video file from your computer, or paste a supported video/VOD link.
4. Start the analysis and wait for it to finish.
5. Open **Highlights** and review the suggestions.
6. Approve the moments you want to turn into clips.
7. Generate and review the clips.
8. Open **Exports** to find the finished MP4 files.

The first analysis may take longer because AutoClip downloads the Whisper `base` speech model (about 150 MB). This is a one-time model download which is cached for later use.

### About YouTube live streams

A live stream that is still broadcasting may remain stuck on **Fetching** or may not be downloadable. This is not necessarily an AutoClip installation problem. For a livestream, record it using a method you are authorised to use, wait until the stream has ended and a VOD is available, or import your own local recording. Private, members-only, age-gated, DRM-protected and otherwise restricted videos may also be unavailable.

## AI providers and API keys

The basic gameplay analysis is designed to work locally. The provider status area may show warnings such as **no API key stored** for Anthropic, OpenAI or Gemini. These warnings are expected if you have not configured those optional providers; they do not automatically mean the application is broken.

Some optional title-refinement or LLM features require an API key from the provider. Each user must use their own key. Never publish API keys in GitHub, screenshots, issue reports or `.env` files.

You can configure a provider through the app's Settings page or the AutoClip command-line configuration tools. See the in-app instructions and [TROUBLESHOOTING.md](TROUBLESHOOTING.md).

## GPU and warning messages

A GPU is not required. AutoClip can process on the CPU.

- **NVENC warning:** FFmpeg may list NVIDIA hardware encoding, but an older NVIDIA driver may not support the NVENC API required by that FFmpeg build. AutoClip tests hardware encoding and falls back to CPU encoding when the test fails. Exports may be slower, but this warning does not by itself stop the app.
- **Intel Quick Sync:** It is used only when the installed FFmpeg build and graphics driver pass a real test encode. Otherwise AutoClip uses CPU encoding.
- **OpenVINO not installed / NPU not detected:** These are optional checks, not requirements for the built-in workflow.
- **Whisper compute type `auto` unsupported:** AutoClip can select a supported CPU compute type instead. A warning that an unsupported override is ignored is not the same as a failed installation.

## Where your files are stored

By default, AutoClip stores its own data under:

```text
C:\Users\YOUR-WINDOWS-USERNAME\.autoclip\
```

This folder contains your imported media, temporary work, database, settings and exported clips. It is separate from the downloaded project folder. **Do not upload this personal data folder to GitHub.**

You can change the data location using `AUTOCLIP_HOME` in your local `.env` file. Do not put private API keys in a public repository.

## Useful files in this repository

| File | What it is for |
|---|---|
| `install.ps1` | Windows installer and environment checker |
| `launch.bat` | Starts AutoClip and opens the browser |
| `INSTALLATION_GUIDE.md` | Beginner-friendly installation instructions |
| `TROUBLESHOOTING.md` | Fixes for common installation and usage problems |
| `GITHUB_UPLOAD_GUIDE.md` | Instructions for the project owner to publish the code |
| `scripts/verify_install.py` | Checks that the installed app and important libraries work |
| `TEST_REPORT.md` | Test coverage and known verification limits |
| `CHANGELOG.md` | Project changes |
| `SECURITY.md` | How to report security issues |

## Developers

The Python backend is in `backend/` and the React/Vite frontend source is in `frontend/`.

The prebuilt web interface is committed under `backend/autoclip/static/` so that end users can run the app without installing Node.js. If you change the frontend source, rebuild it from the `frontend` folder:

```powershell
npm ci
npm run build
```

Install development dependencies and run the automated tests:

```powershell
.venv\Scripts\python.exe -m pip install -e ".[dev]"
.venv\Scripts\python.exe -m pytest -q -m "not slow and not e2e and not golden"
```

See [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) and [`docs/CONTRIBUTING.md`](docs/CONTRIBUTING.md) for more.

## Accuracy and limitations

Highlight detection uses heuristic signals and AI-assisted analysis. A score is an estimate, not a promise of views or audience engagement. Always review suggested clips before publishing.

The project test report documents synthetic test footage and implementation checks. It does **not** establish detection accuracy on all real gameplay, and the included report explicitly identifies checks that still need validation on a Windows computer and real source videos. Read [`TEST_REPORT.md`](TEST_REPORT.md) before treating the test counts as a guarantee.

## Legal and copyright note

Grand Theft Auto and other game names, assets, video footage and trademarks belong to their respective owners. This is an independent video-editing tool and is not affiliated with or endorsed by Rockstar Games. You are responsible for having the rights to any footage, music, images or other material you process and publish.

## License

See [`LICENSE`](LICENSE) and the upstream attribution above. Third-party dependencies, fonts, FFmpeg and AI models may have separate licences and terms.
