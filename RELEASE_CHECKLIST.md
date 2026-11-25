# Public release checklist

Use this checklist before announcing a new AutoClip version.

## Repository safety

- [ ] No real `.env` file or API keys are committed.
- [ ] No `.venv`, `node_modules`, local databases, install logs or private user data are committed.
- [ ] No private source videos or exported clips are committed.
- [ ] `backend/autoclip/static/index.html` and its `assets/` folder are included.
- [ ] Upstream licence and attribution notices are preserved.
- [ ] Repository-specific links in `SECURITY.md` and `pyproject.toml` are reviewed.

## Windows installation

- [ ] Test on Windows 10 or 11, 64-bit.
- [ ] Confirm Python 3.11 or 3.12, 64-bit is detected.
- [ ] Confirm FFmpeg and ffprobe are detected.
- [ ] Run `install.ps1` from a freshly extracted ZIP.
- [ ] Confirm the installer ends with `INSTALLATION SUCCEEDED`.
- [ ] Run `scripts/verify_install.py`.
- [ ] Start the app with `launch.bat`.
- [ ] Confirm the browser opens `http://localhost:8765`.
- [ ] Import a video you have rights to use.
- [ ] Generate and play an exported clip.
- [ ] Confirm a second start works and the app's saved data remains available.

## Documentation

- [ ] README download instructions match the current GitHub page.
- [ ] Installation guide is understandable without developer knowledge.
- [ ] Troubleshooting covers the actual errors seen during testing.
- [ ] Known limitations and supported Python versions are accurate.
- [ ] Release notes explain what changed and what is not yet verified.

## Release honesty

- [ ] Do not claim the app is fully tested on every Windows machine.
- [ ] Do not promise accurate highlights or guaranteed social-media views.
- [ ] Clearly identify optional API keys and any paid provider usage.
- [ ] Remind users to process only media they have rights to use.
