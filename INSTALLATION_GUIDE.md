# AutoClip installation guide (Windows)

This guide is for someone who has never installed a developer tool before. Follow the steps in order. You do not need Git, VS Code, Python knowledge or programming experience.

## Before you start

You need:
- A Windows 10 or Windows 11 computer (64-bit).
- Internet access for setup and the first speech-model download.
- At least 3 GB of free space for the application and its required packages. Video files need extra space.
- A little patience: the first installation can take several minutes.

AutoClip supports **64-bit Python 3.11 or 3.12**. Python 3.13 or newer is not supported by this version.

## Step 1 — Download AutoClip

1. Open the AutoClip repository page on GitHub.
2. Click the green **Code** button.
3. Click **Download ZIP**.
4. Wait for the ZIP file to finish downloading.

You do not need to click **Fork**, **Clone**, or copy any code.

## Step 2 — Extract the ZIP file

1. Open your **Downloads** folder.
2. Find the downloaded AutoClip ZIP file.
3. Right-click it.
4. Choose **Extract All...**.
5. Click **Extract**.
6. Open the new extracted folder named `autoclip`.

Do not try to run the installer while it is still inside the ZIP preview.

## Step 3 — Run the installer

1. Inside the `autoclip` folder, find `install.ps1`.
2. Right-click `install.ps1`.
3. Choose **Run with PowerShell**.
4. A black PowerShell window will open. Keep it open.
5. Read any question it asks. If it offers to install a missing required program such as Python or FFmpeg, choose `Y` to allow that installation, or `N` if you want to install it yourself.
6. Wait. The installer creates a private Python environment and installs the required packages.
7. Look at the final result:
   - **INSTALLATION SUCCEEDED** means setup completed.
   - **INSTALLATION FAILED** means read the error and use [TROUBLESHOOTING.md](TROUBLESHOOTING.md).

The installer may need to download a large number of packages. Do not close the window while it is working.

### If right-click does not show “Run with PowerShell”

1. Open the `autoclip` folder in File Explorer.
2. Click the address bar at the top, type `powershell`, and press Enter.
3. In the PowerShell window, copy and paste this command:

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File ".\install.ps1"
```

4. Press Enter and wait for the result.

This command bypasses the script policy only for this run. It does not permanently change the computer's PowerShell policy.

## Step 4 — Start AutoClip

After you see **INSTALLATION SUCCEEDED**:

1. Return to the `autoclip` folder in File Explorer.
2. Double-click `launch.bat`.
3. A black window opens and starts the local application.
4. Your browser should open automatically.

If the browser does not open, open Chrome, Edge or Firefox and enter:

```text
http://localhost:8765
```

Keep the black launcher window open while you use AutoClip. Closing it or pressing `Ctrl+C` stops the local application.

## Step 5 — Make your first clips

1. In AutoClip, open **Import**.
2. Select a video file from your computer, or paste a supported video/VOD link.
3. Start the analysis.
4. Wait for the analysis to finish.
5. Open **Highlights**.
6. Preview the suggested moments. Approve the ones you want and reject the ones you do not want.
7. Generate clips for the approved moments.
8. Open **Exports** to view and download your finished clips.

The first analysis downloads the Whisper `base` speech model (approximately 150 MB). This can take a while depending on your internet connection. Later runs use the cached model.

## Important: using a YouTube livestream

If you paste a URL for a stream that is **currently live**, the import may remain on “Fetching” or fail. A live broadcast is not the same as a finished video file.

Try one of these:
- Wait until the livestream ends and a VOD is available, then paste the VOD link.
- Record the stream using a method you are allowed to use, then import the recording from your computer.

Do not try to bypass private, age, membership, login or DRM restrictions. Only process material you have permission to download and edit.

## Do I need an AI API key?

No API key is required for the basic local gameplay analysis workflow. Some optional AI-provider features, such as LLM-based title refinement, need your own provider API key.

If the dashboard says an AI provider has **no API key stored**, that is an optional-feature warning. It does not automatically mean the installation failed. Never ask another user to use your private API key, and never put a real key in a public GitHub repository.

## Do I need a gaming GPU?

No. AutoClip can use your CPU. A supported GPU may speed up some operations, but it is optional.

If you see a warning about NVIDIA NVENC, Intel Quick Sync, OpenVINO or an NPU, read [TROUBLESHOOTING.md](TROUBLESHOOTING.md). Optional acceleration warnings do not always mean the application is broken.

## Stop and start the app

- To stop: click the black launcher window and press `Ctrl+C`, or close that window.
- To start again: double-click `launch.bat`.
- Your saved projects and exported clips are kept separately in your Windows user data folder. Stopping the app does not delete them.

## Where are my videos and clips?

By default, AutoClip keeps its data in:

```text
%USERPROFILE%\.autoclip
```

Paste that path into File Explorer's address bar to open it. Inside are the app's database, imported media, temporary work and exports. Keep this folder private and do not upload it to GitHub.

## Still need help?

Read [TROUBLESHOOTING.md](TROUBLESHOOTING.md). If you open a GitHub issue, include the exact error text and the relevant end of `install.log`, but remove API keys, private video links and personal file paths first.
