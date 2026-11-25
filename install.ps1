<#
.SYNOPSIS
    Installs AutoClip, the AI gameplay video clipper, on Windows 10/11.

.DESCRIPTION
    Detects an existing Python 3.11 or 3.12, creates a private virtual environment in
    this folder, installs the dependencies, and verifies the result (Python, every
    critical import, MediaPipe, FFmpeg, folders, config, and app start-up).

    It never changes unrelated system settings and never needs administrator rights.
    Software is installed system-wide (via winget) only if you say yes, and Python is
    never reinstalled when a compatible version is already present.

.PARAMETER PythonPath
    Full path to a specific python.exe (3.11 or 3.12) to use instead of auto-detection.

.PARAMETER DiagnosePython
    Only run Python detection, print exactly what was tried and why each candidate was
    accepted or rejected, then exit. Changes nothing.

.PARAMETER Yes
    Answer "yes" to winget prompts (FFmpeg, Node.js, or Python when none is found).

.PARAMETER Reinstall
    Reinstall dependencies even if they are already installed and unchanged.

.PARAMETER Recreate
    Delete and recreate .venv (the project-local environment only; your videos, settings
    and exported clips are never touched).

.PARAMETER InstallOpenVINO
    Also install the optional OpenVINO runtime (about 100 MB), used only to list Intel
    GPU/NPU devices in `autoclip doctor`.

.EXAMPLE
    powershell.exe -ExecutionPolicy Bypass -File .\install.ps1

.EXAMPLE
    powershell.exe -ExecutionPolicy Bypass -File .\install.ps1 -DiagnosePython

.EXAMPLE
    powershell.exe -ExecutionPolicy Bypass -File .\install.ps1 -PythonPath "C:\Users\You\AppData\Local\Programs\Python\Python311\python.exe"
#>
[CmdletBinding()]
param(
    [string]$PythonPath = '',
    [switch]$DiagnosePython,
    [switch]$Yes,
    [switch]$Reinstall,
    [switch]$Recreate,
    [switch]$InstallOpenVINO
)

$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot
try { Start-Transcript -Path (Join-Path $PSScriptRoot 'install.log') -Append | Out-Null } catch { }

$script:Results = New-Object System.Collections.ArrayList
$script:PyDiag = New-Object System.Collections.ArrayList

function Add-Result([string]$Check, [string]$Status, [string]$Detail) {
    [void]$script:Results.Add([pscustomobject]@{ Check = $Check; Status = $Status; Detail = $Detail })
    $color = 'Gray'
    if ($Status -eq 'OK') { $color = 'Green' }
    elseif ($Status -eq 'WARN') { $color = 'Yellow' }
    elseif ($Status -eq 'FAIL') { $color = 'Red' }
    Write-Host ("  [{0,-4}] {1}: {2}" -f $Status, $Check, $Detail) -ForegroundColor $color
}

function Stop-Install([string]$Message) {
    Add-Result 'Install' 'FAIL' $Message
    Write-Host ''
    Write-Host '== INSTALLATION FAILED ==' -ForegroundColor Red
    Write-Host $Message -ForegroundColor Red
    Write-Host 'Fix the item above and run install.ps1 again. Details are in install.log; see TROUBLESHOOTING.md.' -ForegroundColor Red
    try { Stop-Transcript | Out-Null } catch { }
    exit 1
}

function Confirm-Action([string]$Question) {
    if ($Yes) { return $true }
    $answer = Read-Host "$Question [y/N]"
    return ($answer -match '^(y|yes)$')
}

function Test-Command([string]$Name) {
    return [bool](Get-Command $Name -ErrorAction SilentlyContinue)
}

function Get-FirstLine([string]$Text) {
    $l = ($Text -split "`n" | Where-Object { $_.Trim().Length -gt 0 } | Select-Object -First 1)
    if ($l) { return $l.Trim() }
    return ''
}

function Update-SessionPath {
    $machine = [Environment]::GetEnvironmentVariable('Path', 'Machine')
    $user = [Environment]::GetEnvironmentVariable('Path', 'User')
    $env:Path = "$machine;$user"
}

# Run a native command, capture output and exit code. Windows PowerShell 5.1 turns
# native stderr into terminating errors under $ErrorActionPreference='Stop', so relax
# it just for the call.
#
# IMPORTANT (Windows PowerShell 5.1): embedded double quotes in an argument are
# stripped before the program sees them. Never pass code containing " through here;
# use single quotes, or put the code in a script file.
function Invoke-Native([string]$Exe, [string[]]$Arguments) {
    $previous = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'
    try {
        $output = & $Exe @Arguments 2>&1 | ForEach-Object { "$_" }
        $code = $LASTEXITCODE
    } catch {
        $output = @("$_")
        $code = 1
    } finally {
        $ErrorActionPreference = $previous
    }
    return [pscustomobject]@{ ExitCode = $code; Output = ($output -join "`n") }
}

# Same, but shows output as it arrives (pip can run for minutes and must not look frozen).
function Invoke-NativeLive([string]$Exe, [string[]]$Arguments) {
    $previous = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'
    $lines = New-Object System.Collections.ArrayList
    try {
        & $Exe @Arguments 2>&1 | ForEach-Object {
            $l = "$_"
            [void]$lines.Add($l)
            if ($l.Trim().Length -gt 0) { Write-Host ('    ' + $l) -ForegroundColor DarkGray }
        }
        $code = $LASTEXITCODE
    } catch {
        [void]$lines.Add("$_")
        $code = 1
    } finally {
        $ErrorActionPreference = $previous
    }
    return [pscustomobject]@{ ExitCode = $code; Output = ($lines -join "`n") }
}

# ----------------------------------------------------------- Python detection
#
# Why this exists in this form: the original probe passed Python code containing
# double quotes as an argument. Windows PowerShell 5.1 strips those quotes, Python
# then received broken code, every candidate "failed", and a perfectly good Python
# 3.11 was reported as missing. The probe below contains NO double quotes (only single
# quotes), so it reaches Python intact on every PowerShell version. Spaces are fine:
# PowerShell wraps an argument that contains spaces in quotes correctly.
#
# It also imports venv and ensurepip (a Python that cannot create a virtual
# environment is useless to us) and reports the real sys.executable and bitness.
$script:PyProbeCode = "import sys;import venv,ensurepip;print('%d.%d.%d|%d|%s'%(sys.version_info[0],sys.version_info[1],sys.version_info[2],sys.maxsize>2**32,sys.executable))"

function Add-PyDiag([string]$Source, [string]$Command, [string]$Result) {
    [void]$script:PyDiag.Add([pscustomobject]@{ Source = $Source; Command = $Command; Result = $Result })
}

# The "python.exe" in %LOCALAPPDATA%\Microsoft\WindowsApps is an App Execution Alias,
# not Python. When no Python is installed from the Microsoft Store it only opens the
# Store. Running it as a probe would pop the Store open, so it is skipped unless a
# real Store Python package is installed.
$script:StorePythonChecked = $false
$script:StorePythonInstalled = $false
function Test-StoreAliasPath([string]$Path) {
    $p = ($Path -replace '/', '\')
    return ($p -like '*\Microsoft\WindowsApps\python*.exe')
}
function Test-StorePythonInstalled {
    if (-not $script:StorePythonChecked) {
        $script:StorePythonChecked = $true
        try {
            $pkg = @(Get-AppxPackage -Name 'PythonSoftwareFoundation.Python.*' -ErrorAction Stop)
            $script:StorePythonInstalled = ($pkg.Count -gt 0)
        } catch { $script:StorePythonInstalled = $false }
    }
    return $script:StorePythonInstalled
}

# Run one candidate. Returns a result object if it is a usable Python 3.11/3.12 (64-bit,
# can create virtual environments), otherwise $null after recording exactly why.
function Test-PythonCandidate([string]$Exe, [string[]]$PreArgs, [string]$Source) {
    $label = (@($Exe) + $PreArgs) -join ' '
    $r = Invoke-Native $Exe ($PreArgs + @('-c', $script:PyProbeCode))
    if ($r.ExitCode -ne 0) {
        Add-PyDiag $Source $label ("not usable (exit {0}): {1}" -f $r.ExitCode, (Get-FirstLine $r.Output))
        return $null
    }
    $line = ($r.Output -split "`n" | Where-Object { $_ -match '^\d+\.\d+\.\d+\|[01]\|' } | Select-Object -First 1)
    if (-not $line) {
        Add-PyDiag $Source $label ('ran, but gave unexpected output: ' + (Get-FirstLine $r.Output))
        return $null
    }
    $parts = $line.Trim().Split('|', 3)
    $ver = [version]$parts[0]
    $is64 = ($parts[1] -eq '1')
    $path = $parts[2]
    if (-not ($ver.Major -eq 3 -and ($ver.Minor -eq 11 -or $ver.Minor -eq 12))) {
        Add-PyDiag $Source $label ("found Python $ver at $path, but only 3.11 and 3.12 are supported")
        return $null
    }
    if (-not $is64) {
        Add-PyDiag $Source $label ("found 32-bit Python $ver at $path; 64-bit is required")
        return $null
    }
    Add-PyDiag $Source $label ("OK: Python $ver at $path")
    return [pscustomobject]@{ Version = ('{0}.{1}' -f $ver.Major, $ver.Minor); Full = "$ver"; Path = $path; Via = $Source }
}

# Order of preference: an explicit -PythonPath, the py launcher (3.11 first, then 3.12),
# python.exe files found on PATH, the registry (PEP 514), then the usual install folders.
function Find-Python([string]$Explicit) {
    $script:PyDiag.Clear()

    if ($Explicit) {
        if (Test-Path -LiteralPath $Explicit -PathType Leaf) {
            $c = Test-PythonCandidate $Explicit @() '-PythonPath parameter'
            if ($c) { return $c }
        } else {
            Add-PyDiag '-PythonPath parameter' $Explicit 'file does not exist'
        }
    }

    # 1. Python launcher
    if (Test-Command 'py') {
        foreach ($v in @('3.11', '3.12')) {
            $c = Test-PythonCandidate 'py' @("-$v") "py launcher (-$v)"
            if ($c) { return $c }
        }
    } else {
        Add-PyDiag 'py launcher' 'py' 'not found on PATH'
    }

    # 2. python / python3 on PATH (every match, not only the first)
    foreach ($name in @('python', 'python3')) {
        $cmds = @(Get-Command $name -All -ErrorAction SilentlyContinue | Where-Object { $_.CommandType -eq 'Application' })
        if ($cmds.Count -eq 0) { Add-PyDiag "PATH ($name)" $name 'not found on PATH'; continue }
        foreach ($cmd in $cmds) {
            $src = "$($cmd.Source)"
            if ((Test-StoreAliasPath $src) -and -not (Test-StorePythonInstalled)) {
                Add-PyDiag "PATH ($name)" $src 'skipped: this is the Microsoft Store "App execution alias", not a real Python (it only opens the Store). Ignored on purpose.'
                continue
            }
            $c = Test-PythonCandidate $src @() "PATH ($name)"
            if ($c) { return $c }
        }
    }

    # 3. Registry (PEP 514): where the python.org installer records itself
    foreach ($ver in @('3.11', '3.12')) {
        foreach ($hive in @('HKCU:', 'HKLM:')) {
            foreach ($sub in @("SOFTWARE\Python\PythonCore\$ver\InstallPath", "SOFTWARE\WOW6432Node\Python\PythonCore\$ver\InstallPath")) {
                try {
                    $k = Get-ItemProperty -LiteralPath "$hive\$sub" -ErrorAction Stop
                    $exe = "$($k.ExecutablePath)"
                    if (-not $exe) { $exe = Join-Path "$($k.'(default)')" 'python.exe' }
                    if ($exe -and (Test-Path -LiteralPath $exe -PathType Leaf)) {
                        $c = Test-PythonCandidate $exe @() "registry ($hive $ver)"
                        if ($c) { return $c }
                    }
                } catch { }
            }
        }
    }

    # 4. Default install folders
    $dirs = @()
    if ($env:LOCALAPPDATA) { $dirs += (Join-Path $env:LOCALAPPDATA 'Programs\Python\Python311\python.exe'); $dirs += (Join-Path $env:LOCALAPPDATA 'Programs\Python\Python312\python.exe') }
    if ($env:ProgramFiles) { $dirs += (Join-Path $env:ProgramFiles 'Python311\python.exe'); $dirs += (Join-Path $env:ProgramFiles 'Python312\python.exe') }
    foreach ($d in $dirs) {
        if (Test-Path -LiteralPath $d -PathType Leaf) {
            $c = Test-PythonCandidate $d @() 'default install folder'
            if ($c) { return $c }
        } else {
            Add-PyDiag 'default install folder' $d 'not present'
        }
    }
    return $null
}

function Write-PythonDiagnostics {
    Write-Host ''
    Write-Host '  Python detection report (what was tried and why):' -ForegroundColor Yellow
    $script:PyDiag | ForEach-Object {
        Write-Host ("    - {0}" -f $_.Source) -ForegroundColor Yellow
        Write-Host ("        command: {0}" -f $_.Command) -ForegroundColor DarkGray
        Write-Host ("        result : {0}" -f $_.Result) -ForegroundColor DarkGray
    }
    if (Test-Command 'where.exe') {
        $w = Invoke-Native 'where.exe' @('python')
        Write-Host '  where.exe python:' -ForegroundColor Yellow
        ($w.Output -split "`n") | ForEach-Object { if ($_.Trim()) { Write-Host ('    ' + $_.Trim()) -ForegroundColor DarkGray } }
    }
    if (Test-Command 'py') {
        $l = Invoke-Native 'py' @('-0p')
        Write-Host '  py -0p (installed versions known to the launcher):' -ForegroundColor Yellow
        ($l.Output -split "`n") | ForEach-Object { if ($_.Trim()) { Write-Host ('    ' + $_.Trim()) -ForegroundColor DarkGray } }
    }
    Write-Host ''
}

function Install-WithWinget([string]$Id, [string]$Label) {
    if (-not (Test-Command 'winget')) {
        Write-Host '  winget is not available on this PC.'
        return $false
    }
    Write-Host "  Installing $Label with winget (per-user where supported)..."
    $common = @('--id', $Id, '-e', '--accept-package-agreements', '--accept-source-agreements')
    $r = Invoke-NativeLive 'winget' (@('install') + $common + @('--scope', 'user'))
    if ($r.ExitCode -ne 0) { $r = Invoke-NativeLive 'winget' (@('install') + $common) }
    Update-SessionPath
    # -1978335189 / -1978335212: winget says it is already installed / nothing to do.
    return ($r.ExitCode -eq 0 -or $r.ExitCode -eq -1978335189 -or $r.ExitCode -eq -1978335212)
}

Write-Host ''
Write-Host '== AutoClip: Windows installer ==' -ForegroundColor Cyan
Write-Host ''

# ------------------------------------------------------------ Python only mode
if ($DiagnosePython) {
    Write-Host 'Python detection only (nothing will be changed)' -ForegroundColor Cyan
    $py = Find-Python $PythonPath
    Write-PythonDiagnostics
    if ($py) { Write-Host ("RESULT: usable Python found: {0} at {1} (via {2})" -f $py.Full, $py.Path, $py.Via) -ForegroundColor Green }
    else { Write-Host 'RESULT: no usable Python 3.11/3.12 found (see the report above).' -ForegroundColor Red }
    try { Stop-Transcript | Out-Null } catch { }
    exit 0
}

# ---------------------------------------------------------------- 1. Windows
Write-Host '1. Operating system' -ForegroundColor Cyan
$os = Get-CimInstance Win32_OperatingSystem
$build = [int]$os.BuildNumber
if ($build -ge 22000) { Add-Result 'Windows' 'OK' "$($os.Caption) (build $build)" }
elseif ($build -ge 19041) { Add-Result 'Windows' 'OK' "$($os.Caption) (build $build, Windows 10)" }
else { Add-Result 'Windows' 'WARN' "$($os.Caption) (build $build) is older than Windows 10 2004; untested" }
Add-Result 'PowerShell' 'OK' "$($PSVersionTable.PSVersion)"

# ----------------------------------------------------------------- 2. Storage
Write-Host '2. Storage' -ForegroundColor Cyan
$driveName = (Get-Item -LiteralPath $PSScriptRoot).PSDrive.Name
$freeGb = [math]::Round((Get-PSDrive -Name $driveName).Free / 1GB, 1)
if ($freeGb -lt 3) { Stop-Install "Only $freeGb GB free on drive ${driveName}:. At least 3 GB is needed, plus room for videos and clips." }
elseif ($freeGb -lt 10) { Add-Result 'Disk' 'WARN' "$freeGb GB free on ${driveName}: (install needs about 3 GB; long videos need more)" }
else { Add-Result 'Disk' 'OK' "$freeGb GB free on ${driveName}:" }

# ----------------------------------------------------------------- 3. Hardware
Write-Host '3. Hardware' -ForegroundColor Cyan
$cpu = Get-CimInstance Win32_Processor | Select-Object -First 1
$ramGb = [math]::Round((Get-CimInstance Win32_ComputerSystem).TotalPhysicalMemory / 1GB, 1)
Add-Result 'CPU' 'OK' "$($cpu.Name.Trim()) ($($cpu.NumberOfCores) cores, $($cpu.NumberOfLogicalProcessors) threads)"
if ($ramGb -lt 8) { Add-Result 'RAM' 'WARN' "$ramGb GB. Use the Low preset and short videos" }
else { Add-Result 'RAM' 'OK' "$ramGb GB" }

$gpus = @(Get-CimInstance Win32_VideoController)
$hasNvidia = $false
$hasIntel = $false
foreach ($g in $gpus) {
    $name = "$($g.Name)"
    if ($name -match 'NVIDIA') { $hasNvidia = $true }
    if ($name -match 'Intel') { $hasIntel = $true }
    # AdapterRAM is a 32-bit field and describes shared memory on integrated graphics,
    # so it is deliberately not reported as VRAM.
    Add-Result 'Graphics' 'OK' "$name (driver $($g.DriverVersion))"
}
if ($hasNvidia) {
    if (Test-Command 'nvidia-smi') { Add-Result 'NVIDIA CUDA' 'OK' 'nvidia-smi found (optional; not required)' }
    else { Add-Result 'NVIDIA CUDA' 'WARN' 'NVIDIA GPU listed but nvidia-smi not found; running on CPU' }
} else {
    Add-Result 'NVIDIA CUDA' 'OK' 'not present; everything runs on CPU (default, fully supported)'
}

# ------------------------------------------------------------------- 4. Python
Write-Host '4. Python 3.11 or 3.12' -ForegroundColor Cyan
$py = Find-Python $PythonPath
if (-not $py) {
    Write-PythonDiagnostics
    $anyPython = ($script:PyDiag | Where-Object { $_.Result -like 'OK*' -or $_.Result -like 'found *' })
    Write-Host '  No usable Python 3.11/3.12 was found.' -ForegroundColor Yellow
    Write-Host '  (Python 3.13+ is not supported: MediaPipe has no wheels for it.)'
    Write-Host '  If you DO have Python 3.11 installed, do not reinstall it: rerun with its full path:'
    Write-Host '    .\install.ps1 -PythonPath "C:\path\to\Python311\python.exe"' -ForegroundColor Cyan
    if (Confirm-Action 'Otherwise, install Python 3.11 with winget now?') {
        if (Install-WithWinget 'Python.Python.3.11' 'Python 3.11') { $py = Find-Python $PythonPath }
        if (-not $py) { Write-PythonDiagnostics }
    }
}
if (-not $py) {
    Stop-Install 'No usable Python 3.11 or 3.12. See the detection report above. Install Python 3.11 from https://www.python.org/downloads/ (tick "Add python.exe to PATH") and open a NEW terminal, or pass -PythonPath.'
}
Add-Result 'Python' 'OK' "$($py.Full) at $($py.Path) (found via $($py.Via))"

# ------------------------------------------------------------------- 5. FFmpeg
Write-Host '5. FFmpeg' -ForegroundColor Cyan
if (-not (Test-Command 'ffmpeg')) {
    Write-Host '  FFmpeg not found.'
    if (Confirm-Action 'Install FFmpeg (Gyan.FFmpeg full build) with winget?') { [void](Install-WithWinget 'Gyan.FFmpeg' 'FFmpeg') }
}
if (-not (Test-Command 'ffmpeg')) {
    Stop-Install 'FFmpeg is required. Run: winget install Gyan.FFmpeg   then open a NEW terminal and run install.ps1 again.'
}
$ff = Invoke-Native 'ffmpeg' @('-hide_banner', '-version')
Add-Result 'FFmpeg' 'OK' (Get-FirstLine $ff.Output)
if (-not (Test-Command 'ffprobe')) { Stop-Install 'ffprobe was not found next to FFmpeg. Install the full build: winget install Gyan.FFmpeg' }

$filters = Invoke-Native 'ffmpeg' @('-hide_banner', '-filters')
if ($filters.Output -match '(?m)^\s*\S+\s+ass\s') { Add-Result 'FFmpeg libass' 'OK' 'subtitle burn-in supported' }
else { Stop-Install 'This FFmpeg build has no libass, so captions cannot be burned in. Install the full build: winget install Gyan.FFmpeg' }

$enc = Invoke-Native 'ffmpeg' @('-hide_banner', '-encoders')
if ($enc.Output -match 'libx264') { Add-Result 'x264 encoder' 'OK' 'CPU H.264 encoding available (default)' }
else { Stop-Install 'This FFmpeg build has no libx264 encoder.' }

# Quick Sync is only reported usable if a real one-frame encode succeeds.
if ($enc.Output -match 'h264_qsv') {
    $qsv = Invoke-Native 'ffmpeg' @('-hide_banner', '-nostdin', '-f', 'lavfi', '-i', 'color=c=black:s=256x256:d=0.2', '-c:v', 'h264_qsv', '-pix_fmt', 'nv12', '-frames:v', '1', '-f', 'null', '-')
    if ($qsv.ExitCode -eq 0) { Add-Result 'Intel Quick Sync' 'OK' 'test encode succeeded; exports will use it (falls back to CPU if a render fails)' }
    elseif ($hasIntel) { Add-Result 'Intel Quick Sync' 'WARN' 'Intel graphics found but the h264_qsv test encode failed; using CPU. Updating the Intel graphics driver may help' }
    else { Add-Result 'Intel Quick Sync' 'OK' 'not usable on this machine; using CPU' }
} else {
    Add-Result 'Intel Quick Sync' 'OK' 'this FFmpeg build has no h264_qsv; using CPU'
}

# -------------------------------------------------------------- 6. Node and Git
Write-Host '6. Node.js and Git' -ForegroundColor Cyan
$uiBuilt = Test-Path -LiteralPath (Join-Path $PSScriptRoot 'backend\autoclip\static\index.html')
if (Test-Command 'node') { Add-Result 'Node.js' 'OK' ((Invoke-Native 'node' @('--version')).Output.Trim()) }
elseif ($uiBuilt) { Add-Result 'Node.js' 'OK' 'not installed, and not needed: the web interface is already built' }
else {
    Write-Host '  The web interface is not built and Node.js is not installed.'
    if (Confirm-Action 'Install Node.js LTS with winget?') { [void](Install-WithWinget 'OpenJS.NodeJS.LTS' 'Node.js LTS') }
    if (-not (Test-Command 'node')) { Stop-Install 'Node.js LTS is required to build the interface. Install from https://nodejs.org (LTS) then run install.ps1 again.' }
    Add-Result 'Node.js' 'OK' ((Invoke-Native 'node' @('--version')).Output.Trim())
}
if (Test-Command 'git') { Add-Result 'Git' 'OK' ((Invoke-Native 'git' @('--version')).Output.Trim() + ' (only needed to update the source)') }
else { Add-Result 'Git' 'OK' 'not installed (only needed to update the source)' }

# ------------------------------------------------------------- 7. Virtual env
Write-Host '7. Python environment' -ForegroundColor Cyan
$venv = Join-Path $PSScriptRoot '.venv'
$venvPy = Join-Path $venv 'Scripts\python.exe'
$stampPath = Join-Path $venv 'autoclip-install.json'

if ($Recreate -and (Test-Path -LiteralPath $venv)) {
    Write-Host '  Removing the existing .venv (project-local only)...'
    Remove-Item -LiteralPath $venv -Recurse -Force
}
# A venv left by an earlier attempt can be broken or built on an unsupported Python.
if (Test-Path -LiteralPath $venvPy) {
    $vcheck = Test-PythonCandidate $venvPy @() 'existing .venv'
    if (-not $vcheck) {
        Write-Host '  The existing .venv is unusable (see report); recreating it.' -ForegroundColor Yellow
        Remove-Item -LiteralPath $venv -Recurse -Force
    }
}
if (-not (Test-Path -LiteralPath $venvPy)) {
    Write-Host '  Creating .venv ...'
    $r = Invoke-Native $py.Path @('-m', 'venv', $venv)
    if ($r.ExitCode -ne 0 -or -not (Test-Path -LiteralPath $venvPy)) { Stop-Install "Could not create the virtual environment with $($py.Path).`n$($r.Output)" }
    Add-Result 'Virtual environment' 'OK' "created at $venv"
} else {
    Add-Result 'Virtual environment' 'OK' "reusing $venv"
}

# Skip pip when requirements and the Python version are unchanged since the last good install.
$reqHash = (Get-FileHash -LiteralPath (Join-Path $PSScriptRoot 'requirements.txt') -Algorithm SHA256).Hash
$projHash = (Get-FileHash -LiteralPath (Join-Path $PSScriptRoot 'pyproject.toml') -Algorithm SHA256).Hash
$upToDate = $false
if ((-not $Reinstall) -and (Test-Path -LiteralPath $stampPath)) {
    try {
        $stamp = Get-Content -LiteralPath $stampPath -Raw | ConvertFrom-Json
        if ($stamp.requirements -eq $reqHash -and $stamp.pyproject -eq $projHash -and $stamp.python -eq $py.Full) { $upToDate = $true }
    } catch { $upToDate = $false }
}

if ($upToDate) {
    Add-Result 'Dependencies' 'OK' 'already installed and unchanged (use -Reinstall to force)'
} else {
    Write-Host '  Installing dependencies. The first time this downloads a few hundred MB and can take several minutes.'
    [void](Invoke-Native $venvPy @('-m', 'pip', 'install', '--upgrade', 'pip', '--quiet', '--disable-pip-version-check'))
    # Binary wheels only: nothing here should ever need a compiler on Windows.
    $pip = Invoke-NativeLive $venvPy @('-m', 'pip', 'install', '-r', 'requirements.txt', '--only-binary=:all:', '--progress-bar', 'off', '--disable-pip-version-check')
    if ($pip.ExitCode -eq 0) {
        $pip = Invoke-NativeLive $venvPy @('-m', 'pip', 'install', '-e', '.', '--no-deps', '--progress-bar', 'off', '--disable-pip-version-check')
    } else {
        Write-Host '  The pinned versions did not install on this Python; resolving compatible versions instead...' -ForegroundColor Yellow
        $pip = Invoke-NativeLive $venvPy @('-m', 'pip', 'install', '-e', '.', '--progress-bar', 'off', '--disable-pip-version-check')
    }
    if ($pip.ExitCode -ne 0) {
        $tail = ($pip.Output -split "`n" | Select-Object -Last 15) -join "`n"
        Stop-Install "pip failed to install the dependencies. Last output:`n$tail"
    }
    Add-Result 'Dependencies' 'OK' 'installed'
}

if ($InstallOpenVINO) {
    $ov = Invoke-NativeLive $venvPy @('-m', 'pip', 'install', 'openvino', '--progress-bar', 'off', '--disable-pip-version-check')
    if ($ov.ExitCode -eq 0) { Add-Result 'OpenVINO' 'OK' 'installed (optional; lists Intel devices only)' }
    else { Add-Result 'OpenVINO' 'WARN' 'could not install; not needed' }
} else {
    Add-Result 'OpenVINO' 'OK' 'skipped (optional; add with -InstallOpenVINO)'
}

# Dependency conflicts are reported, never "fixed" by random upgrades.
$chk = Invoke-Native $venvPy @('-m', 'pip', 'check', '--disable-pip-version-check')
if ($chk.ExitCode -eq 0) { Add-Result 'Dependency conflicts' 'OK' 'pip check: none' }
else { Add-Result 'Dependency conflicts' 'WARN' ("pip check reported: " + (Get-FirstLine $chk.Output) + ' (details in install.log)'); Write-Host $chk.Output }

# ---------------------------------------------------------------- 8. Web UI
$uiBuilt = Test-Path -LiteralPath (Join-Path $PSScriptRoot 'backend\autoclip\static\index.html')
if (-not $uiBuilt) {
    Write-Host '  Building the web interface...'
    Push-Location (Join-Path $PSScriptRoot 'frontend')
    try {
        $npm = Invoke-NativeLive 'npm' @('ci', '--no-audit', '--no-fund')
        if ($npm.ExitCode -eq 0) { $npm = Invoke-NativeLive 'npm' @('run', 'build') }
    } finally { Pop-Location }
    if ($npm.ExitCode -ne 0) { Stop-Install "The interface build failed.`n$($npm.Output)" }
}
Add-Result 'Web interface' 'OK' 'built'

# ------------------------------------------------------------- 9. Configuration
Write-Host '8. Configuration' -ForegroundColor Cyan
if (-not (Test-Path -LiteralPath (Join-Path $PSScriptRoot '.env'))) {
    Copy-Item -LiteralPath (Join-Path $PSScriptRoot '.env.example') -Destination (Join-Path $PSScriptRoot '.env')
    Add-Result '.env' 'OK' 'created from .env.example'
} else { Add-Result '.env' 'OK' 'already present (left untouched)' }
Add-Result 'config.yaml' 'OK' 'defaults: Low preset (Whisper base, about 1 FPS sampling, CPU)'

# ---------------------------------------------------------------- 10. Verify
Write-Host '9. Verification (imports, MediaPipe, FFmpeg, folders, app start)' -ForegroundColor Cyan
$ver = Invoke-NativeLive $venvPy @((Join-Path $PSScriptRoot 'scripts\verify_install.py'))
if ($ver.ExitCode -ne 0) {
    Stop-Install 'The post-install verification found critical problems (lines marked FAIL above).'
}
Add-Result 'Verification' 'OK' 'all critical checks passed (scripts\verify_install.py)'

# Remember what was installed so a re-run can skip pip.
try {
    @{ python = $py.Full; requirements = $reqHash; pyproject = $projHash; installed = (Get-Date).ToString('s') } |
        ConvertTo-Json | Set-Content -LiteralPath $stampPath -Encoding ASCII
} catch { }

# -------------------------------------------------------------------- Summary
Write-Host ''
Write-Host '== Summary ==' -ForegroundColor Cyan
$script:Results | Format-Table -AutoSize | Out-String | Write-Host
$warns = @($script:Results | Where-Object { $_.Status -eq 'WARN' })
Write-Host '== INSTALLATION SUCCEEDED ==' -ForegroundColor Green
if ($warns.Count -gt 0) { Write-Host ("{0} optional warning(s) above; none block the app." -f $warns.Count) -ForegroundColor Yellow }
Write-Host 'Start the app by double-clicking launch.bat (it opens http://localhost:8765).'
Write-Host 'The first analysis downloads the Whisper "base" speech model (about 150 MB) once.'
Write-Host 'Only analyse footage you own or have permission to edit.'
try { Stop-Transcript | Out-Null } catch { }
