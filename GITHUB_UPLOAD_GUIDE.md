# Publish AutoClip to GitHub (project owner)

This guide is for the person maintaining the project. It is separate from the beginner installation guide.

## 1. Create the repository on GitHub

1. Sign in to GitHub.
2. Open <https://github.com/new>.
3. Repository name: `AutoClip-AI-Video-Clipper` (or choose another name).
4. Description: `Local-first AI gameplay video clipper for turning long videos into short highlights.`
5. Choose **Public** if you want everyone to download it.
6. Leave **Add a README**, **Add .gitignore** and **Choose a license** unchecked. This project already contains those files.
7. Click **Create repository**.

Keep the new repository page open. GitHub will show its URL, similar to:

```text
https://github.com/YOUR-USERNAME/AutoClip-AI-Video-Clipper.git
```

## 2. Open PowerShell in the extracted `autoclip` folder

Open File Explorer and open the folder that contains `README.md`, `install.ps1` and `launch.bat`.

Click the address bar, type `powershell`, and press Enter.

Check that you are in the correct folder:

```powershell
Get-Location
Get-ChildItem
```

You should see `README.md`, `install.ps1`, `launch.bat`, `backend`, `frontend` and other project files.

## 3. Check Git is installed

Run:

```powershell
git --version
```

If Windows says Git is not recognised, install Git for Windows from <https://git-scm.com/download/win>, then close and reopen PowerShell.

## 4. Prepare the repository

Run these commands one at a time:

```powershell
git init
git branch -M main
git status
```

Read the status output. It lists files Git is preparing to track.

### Check for private files before uploading

The `.gitignore` file excludes virtual environments, Node dependencies, personal databases, local media, exports, logs and `.env` files.

Before continuing, make sure you do not see any of these in the files you intend to upload:

- `.env` containing real API keys.
- `.venv` or `node_modules`.
- Your personal `%USERPROFILE%\.autoclip` data folder.
- Personal source videos, private recordings or generated exports.
- `autoclip.db`, `*.sqlite` or other personal databases.
- `install.log` if it contains private paths or other personal details.

The compiled web interface under `backend/autoclip/static/` **must be included**. It allows normal Windows users to run AutoClip without installing Node.js.

If a real API key was ever committed, deleting the file in a later commit is not enough. Revoke/rotate that key with its provider immediately.

## 5. Create the first commit

When the file list looks correct:

```powershell
git add .
git status
```

Review the staged file list again. Then create the commit:

```powershell
git commit -m "Prepare AutoClip for public release"
```

If Git asks for your name and email, set the name and email you want associated with your Git commits:

```powershell
git config --global user.name "Your Name"
git config --global user.email "you@example.com"
```

Use an email address you are comfortable associating with public commits. Then run the commit command again.

## 6. Connect the local folder to GitHub and upload

Replace the example URL with the URL shown on your new GitHub repository page:

```powershell
git remote add origin https://github.com/YOUR-USERNAME/AutoClip-AI-Video-Clipper.git
git push -u origin main
```

GitHub may open a browser window so you can sign in. When the command finishes successfully, refresh the GitHub repository page. Your project files should appear there.

If Git says `remote origin already exists`, inspect it:

```powershell
git remote -v
```

If it points to the wrong repository, correct it:

```powershell
git remote set-url origin https://github.com/YOUR-USERNAME/AutoClip-AI-Video-Clipper.git
git push -u origin main
```

## 7. Update repository links after creating the repository

Before publishing, search the project for upstream links that point to `artbyjazi/autoclip`. Some links are intentionally kept as upstream attribution. Update only links that should point to your new repository, for example:

- The security reporting link in `SECURITY.md`.
- The repository and issue links in `pyproject.toml`, if you are maintaining this fork as its own package.
- Any issue or contribution links you add to the README.

Do not remove the original upstream attribution or the copyright/licence notice from the existing `LICENSE` file.

## 8. Test the public download

Do not assume that a successful Git push means the download works.

1. Open your public GitHub repository in a browser.
2. Click **Code → Download ZIP**.
3. Save and extract the ZIP into a new folder.
4. Check that `install.ps1`, `launch.bat`, `README.md` and `backend/autoclip/static/` are present.
5. Test the installation on a clean Windows account or another Windows computer if possible.
6. Run `install.ps1`, wait for **INSTALLATION SUCCEEDED**, then run `launch.bat`.

This catches missing files that might have been accidentally ignored by `.gitignore`.

## 9. Create a versioned release (recommended)

After the clean-download test succeeds:

1. Open the repository on GitHub.
2. Select **Releases**.
3. Choose **Draft a new release**.
4. Create a tag such as `v0.1.0`.
5. Give the release a title such as `AutoClip v0.1.0 – First public preview`.
6. In the release notes, explain that this is an early preview and mention known limitations.
7. Publish the release.

A GitHub Release gives users a clear version to download. For a first release, do not claim production-ready reliability until you have tested the installer and real footage on supported Windows computers.

## 10. Upload future changes

After you edit files, run:

```powershell
git status
git add .
git status
git commit -m "Describe what changed"
git push
```

Always review `git status` before `git add .` and before pushing. Never commit secrets, personal videos, databases or exports.
