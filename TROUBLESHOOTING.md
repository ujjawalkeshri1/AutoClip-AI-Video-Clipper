# Troubleshooting AutoClip

This guide explains common issues in plain language. Start with the section that matches what you see.

## First: check whether installation actually finished

Open PowerShell in the `autoclip` project folder and run:

```powershell
.\.venv\Scripts\autoclip.exe doctor
```

This checks Python packages, FFmpeg, hardware acceleration and optional providers.

To run the detailed installation verification again:

```powershell
.\.venv\Scripts\python.exe .\scripts\verify_install.py
```

The installer also saves a log file named `install.log` in the project folder. Look at the last lines for the first `[FAIL]` entry.

## Installation problems

### “Running scripts is disabled on this system”

Windows PowerShell may block scripts by default. Open PowerShell in the project folder and run:

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File ".\install.ps1"
```

This bypass applies to this command only. It does not permanently change your PowerShell security policy.

### “Python 3.11/3.12 not found” even though Python is installed

AutoClip needs **64-bit Python 3.11 or 3.12**. Python 3.13+ and 32-bit Python are not supported by this release.

First, ask the installer what it can find:

```powershell
.\install.ps1 -DiagnosePython
```

This is a diagnostic check and does not install or remove anything. If you know where compatible Python is installed, pass its exact `python.exe` path:

```powershell
.\install.ps1 -PythonPath "C:\Users\YOUR-NAME\AppData\Local\Programs\Python\Python311\python.exe"
```

Replace the example path with the real path on your computer.

### A Python window opens the Microsoft Store

Windows sometimes has a fake `python.exe` shortcut called an **App Execution Alias**. It can open the Microsoft Store instead of running Python.

The installer is designed to recognise and skip that shortcut. If needed, go to:

**Windows Settings → Apps → Advanced app settings → App execution aliases**

Turn off the `python.exe` and `python3.exe` aliases listed under **App Installer**. Then open a new PowerShell window and try the installer again.

### FFmpeg or ffprobe is missing

FFmpeg is a helper program AutoClip uses to read and create videos. It must include the caption and H.264 features.

The recommended Windows package is the full Gyan FFmpeg build. The installer can offer to install it using `winget`. If you install FFmpeg yourself, make sure both `ffmpeg.exe` and `ffprobe.exe` are available on PATH.

After installing FFmpeg:
1. Close PowerShell windows that were open before the installation.
2. Open a new PowerShell window.
3. Run the installer again.

Windows terminals opened before a PATH change may not see the newly installed program.

### “Verification failed” after packages were installed

The installer runs a final check of the important Python libraries, FFmpeg, the configuration file and the application startup. Find the first line marked `[FAIL]` in the PowerShell output or `install.log`.

You can run the check again with:

```powershell
.\.venv\Scripts\python.exe .\scripts\verify_install.py
```

A line marked `[WARN]` may describe an optional feature. A `[FAIL]` line is the one to investigate.

### `PermissionError: [WinError 32]` or “file is being used by another process”

Windows is saying that a file is still open in another program. This can happen if AutoClip, a previous verification run, or a Python process is still using a temporary database file.

Try this in order:

1. Close the AutoClip browser tab.
2. Stop the AutoClip launcher window with `Ctrl+C`, or close that window.
3. Close any other PowerShell window that is running AutoClip or its tests.
4. Wait a few seconds and run `install.ps1` again.
5. If Windows still says the file is in use, restart the computer and run the installer again.

**Do not delete `%USERPROFILE%\.autoclip\autoclip.db` to fix a temporary-file lock.** That database can contain your saved project history. Do not delete your `.autoclip` data folder.

### Installer says Python is found, but package installation fails

The installer needs to download packages from Python's package service. A weak internet connection, antivirus, VPN or company network can interrupt downloads.

- Check that your internet connection works.
- Retry the installer.
- Check the final error in `install.log`.
- If your antivirus or company network blocks Python package downloads, ask the network administrator before changing security settings.

To rebuild only the project's private Python environment, run:

```powershell
.\install.ps1 -Recreate
```

This recreates the `.venv` folder in the project. It is not supposed to delete your separate AutoClip data folder under `%USERPROFILE%\.autoclip`.

### Not enough disk space

The installation needs several gigabytes because it installs video, audio and machine-learning libraries. The first speech model download needs additional space. Your own source videos and exported clips need more space again.

Free up disk space and run the installer again.

## Starting problems

### `launch.bat` says “The app is not installed yet”

The installer has not completed, or the `.venv` folder is missing. Run `install.ps1` and wait for **INSTALLATION SUCCEEDED** before running `launch.bat`.

### The browser does not open

Wait a few seconds, then open a browser yourself and visit:

```text
http://localhost:8765
```

The black launcher window must remain open. If it has closed, start AutoClip again using `launch.bat`.

### Port 8765 is already in use

Another application may already be using the same local port. If AutoClip is already running, `launch.bat` should open that existing instance. Otherwise, close the other application using that port or change `AUTOCLIP_PORT` in your local `.env` file, then restart AutoClip.

### The page is blank or says the frontend is missing

The built web interface is included in this repository at `backend/autoclip/static/`. If that folder was deleted or the frontend was changed without rebuilding it, a developer can rebuild it:

```powershell
cd frontend
npm ci
npm run build
cd ..
```

Node.js LTS is only needed for rebuilding the frontend, not for a normal end-user installation.

## AI provider and hardware warnings

### “No API key stored” for Anthropic, OpenAI or Gemini

This is expected if you have not configured an optional cloud AI provider. The core local gameplay analysis does not require a paid provider key. Optional LLM title refinement needs your own API key.

Do not paste API keys into public GitHub issues or share them with other users.

### “OpenVINO is not installed” or “NPU not detected”

OpenVINO and NPU detection are optional. Their absence does not stop the core pipeline.

### “NVENC initialisation failed” or NVIDIA driver is too old

FFmpeg can list NVIDIA encoding support even when the installed graphics driver cannot use it. AutoClip performs a small test encode. If it fails, AutoClip uses CPU H.264 encoding instead. The result can still be exported; it may simply take longer.

Update your NVIDIA driver if you want to try enabling NVENC, then run `autoclip doctor` again.

### Whisper says compute type `auto` is unsupported

Some computers do not support every Whisper compute type. AutoClip checks the types supported by the installed runtime and can choose a compatible CPU option. If the doctor report says the override is ignored and the overall status is **Ready**, this warning alone does not mean installation failed.

### Whisper model download is slow or fails

The first analysis downloads the Whisper `base` model (around 150 MB). Check your internet connection and try again. The model is cached after download.

## Import and analysis problems

### YouTube import stays on “Fetching”

If the URL is a **currently live** YouTube stream, it may not be downloadable while the broadcast is in progress. Wait for the stream to finish and use its VOD, or record it using a method you are authorised to use and import the local recording.

Private, members-only, age-gated, DRM-protected and login-restricted videos may not be available. AutoClip does not bypass those restrictions.

For a normal public VOD that used to work but no longer imports, update yt-dlp inside the environment:

```powershell
.\.venv\Scripts\python.exe -m pip install --upgrade yt-dlp
```

Then restart AutoClip.

### “No moments stood out”

The detector compares moments with the rest of the video. A quiet or very repetitive video may not contain strong candidates. Try a longer or more eventful recording, check the minimum-score filter, or select a higher analysis preset.

### The first analysis takes a long time

The first run may download the Whisper model and initialise the video/audio libraries. Later analyses can reuse cached files. Low resource mode is slower but uses less memory.

### No captions appear

AutoClip does not invent dialogue. If nobody speaks in a clip, there may be no speech captions. You can enable the opening hook text option if you want a short contextual line.

### Computer becomes slow or runs out of memory

Choose Low resource mode, close other heavy applications, and avoid enabling optional vision analysis on long videos. Larger Whisper models need more memory.

## Export problems

### Export is slow

Your computer may be using CPU encoding because hardware acceleration did not pass the test. CPU encoding is supported but can take longer.

### Export fails

Check that the drive containing your AutoClip data has enough free space. Restart AutoClip and try the export again. If it continues to fail, copy the exact error message and the relevant log details into a GitHub issue after removing private information.

### The crop cuts off the minimap or important game information

In the clip editor, try **Whole frame** (with blurred background) instead of a tight vertical crop. You can also leave framing on Auto or adjust the crop for that clip.

## Still stuck?

When opening a GitHub issue, include:

- Windows version.
- Whether you used Python 3.11 or 3.12.
- The first `[FAIL]` line from the installer or doctor output.
- The last relevant lines from `install.log`.
- What you clicked just before the problem happened.

Before sharing logs, remove API keys, private URLs, usernames and personal folder paths. Never upload your private videos, database or `.env` file.
