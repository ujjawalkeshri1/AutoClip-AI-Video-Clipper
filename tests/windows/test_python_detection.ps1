<#
Behavioural tests for install.ps1's Python detection. Runs on any PowerShell 7 (even Linux/macOS)
by extracting the real functions from install.ps1 and exercising them against fake interpreters.

Run it twice: once with the default argument passing and once emulating Windows PowerShell 5.1,
which is where the original bug lived (it strips double quotes from native-command arguments):

    pwsh -File test_python_detection.ps1 -Mode Standard
    pwsh -File test_python_detection.ps1 -Mode Legacy

This does NOT run Windows-only code (registry, winget, Appx). Those paths are reviewed, not executed.
#>
param([ValidateSet('Standard', 'Legacy')][string]$Mode = 'Standard', [string]$Script = (Join-Path $PSScriptRoot '..\..\install.ps1'))
$PSNativeCommandArgumentPassing = $Mode
$ErrorActionPreference = 'Stop'
$t = $null; $e = $null
$ast = [System.Management.Automation.Language.Parser]::ParseFile((Resolve-Path $Script), [ref]$t, [ref]$e)
$wanted = 'Invoke-Native', 'Test-Command', 'Get-FirstLine', 'Add-PyDiag', 'Test-StoreAliasPath', 'Test-StorePythonInstalled', 'Test-PythonCandidate', 'Find-Python'
foreach ($f in $ast.FindAll({ param($n) $n -is [System.Management.Automation.Language.FunctionDefinitionAst] }, $true)) {
    if ($f.Name -in $wanted) { . ([scriptblock]::Create($f.Extent.Text)) }
}
# the $script:PyProbeCode assignment (a top-level statement, not a function)
$assign = $ast.FindAll({ param($n) $n -is [System.Management.Automation.Language.AssignmentStatementAst] -and $n.Left.Extent.Text -eq '$script:PyProbeCode' }, $true) | Select-Object -First 1
. ([scriptblock]::Create($assign.Extent.Text))
$script:PyDiag = New-Object System.Collections.ArrayList
$script:StorePythonChecked = $false; $script:StorePythonInstalled = $false

$root = Join-Path ([IO.Path]::GetTempPath()) ("pydetect_" + [guid]::NewGuid().ToString('N'))
New-Item -ItemType Directory -Path $root | Out-Null
$real = (Get-Command python3 -ErrorAction Stop).Source
$script:fail = 0

# A fake interpreter: answers the probe with a canned line, and logs every invocation to a file.
function New-Fake([string]$Dir, [string]$Name, [string]$ProbeLine, [int]$ExitCode = 0) {
    New-Item -ItemType Directory -Force -Path $Dir | Out-Null
    $log = Join-Path $Dir "$Name.calls"
    $body = "#!/bin/sh`necho `"`$@`" >> '$log'`nif [ `"`$1`" = `"-c`" ] || [ `"`$2`" = `"-c`" ]; then`n  " +
        $(if ($ExitCode -eq 0) { "echo '$ProbeLine'" } else { "echo 'Python was not found; run without arguments to install from the Microsoft Store' >&2" }) +
        "`n  exit $ExitCode`nfi`nexit 0`n"
    $p = Join-Path $Dir $Name
    Set-Content -LiteralPath $p -Value $body -NoNewline
    [System.IO.File]::SetUnixFileMode($p, 'UserRead,UserWrite,UserExecute')
    return $log
}
# PATH is *only* the given directories, so this machine's real python3 cannot leak into a scenario.
function Use-Path([string[]]$Dirs) { $env:PATH = ($Dirs -join [IO.Path]::PathSeparator) }
function Check([string]$Name, [bool]$Ok, [string]$Detail = '') {
    if ($Ok) { Write-Host ("  PASS  {0}" -f $Name) -ForegroundColor Green }
    else { Write-Host ("  FAIL  {0}  {1}" -f $Name, $Detail) -ForegroundColor Red; $script:fail++ }
}
Write-Host "== Python detection tests (argument passing: $Mode) =="

# --- Control: the ORIGINAL probe, with embedded double quotes.
$oldProbe = 'import sys; print("%d.%d|%s" % (sys.version_info[0], sys.version_info[1], sys.executable))'
$o = Invoke-Native $real @('-c', $oldProbe)
if ($Mode -eq 'Legacy') { Check 'control: the original quoted probe is broken under 5.1-style passing' ($o.ExitCode -ne 0) "exit=$($o.ExitCode)" }
else { Check 'control: the original quoted probe works under standard passing (why it was missed on Linux)' ($o.ExitCode -eq 0) "exit=$($o.ExitCode)" }

# --- 1. The new probe survives intact in both modes.
$n = Invoke-Native $real @('-c', $script:PyProbeCode)
Check 'new probe contains no double quotes (the 5.1 failure mode)' ($script:PyProbeCode -notmatch '"')
Check 'new probe runs and reports version|64bit|path' (($n.ExitCode -eq 0) -and ($n.Output -match '^\d+\.\d+\.\d+\|1\|')) $n.Output

# --- 2. A normal install is found (py launcher passes the -3.11 selector through).
$bin = Join-Path $root 'ok'; [void](New-Fake $bin 'py' '3.11.9|1|C:\Users\Ujjawal Keshri\AppData\Local\Programs\Python\Python311\python.exe')
Use-Path @($bin)
$r = Find-Python ''
Check 'py launcher: Python 3.11.9 in a path WITH A SPACE is detected' ($r -and $r.Full -eq '3.11.9' -and $r.Path -like '*Ujjawal Keshri*' -and $r.Version -eq '3.11') ($r | Out-String)
Check 'py launcher is preferred and the real sys.executable is used' ($r.Via -like 'py launcher*')

# --- 3. Microsoft Store alias only: must be skipped WITHOUT being executed, and clearly explained.
$store = Join-Path $root 'Microsoft/WindowsApps'; $stubLog = New-Fake $store 'python' '' 9009
function Test-StoreAliasPath([string]$Path) { return ($Path -like '*/Microsoft/WindowsApps/python*') }   # Linux-path stand-in for the real *\Microsoft\WindowsApps\python*.exe test
Use-Path @($store)
$script:PyDiag.Clear(); $r = Find-Python ''
Check 'store alias alone: reports not found' ($null -eq $r)
Check 'store alias was NEVER executed (so it cannot pop open the Microsoft Store)' (-not (Test-Path $stubLog))
Check 'store alias: diagnostic explains why it was skipped' (($script:PyDiag | Where-Object { $_.Result -like '*App execution alias*' }).Count -ge 1)

# --- 4. Store alias EARLIER on PATH than a real python: the real one must still be found.
$real_dir = Join-Path $root 'real'; [void](New-Fake $real_dir 'python' '3.11.9|1|C:\Users\Ujjawal Keshri\AppData\Local\Programs\Python\Python311\python.exe')
Use-Path @($store, $real_dir)
$r = Find-Python ''
Check 'alias first on PATH, real Python second: real Python 3.11.9 found' ($r -and $r.Full -eq '3.11.9') ($r | Out-String)
Check 'alias still not executed' (-not (Test-Path $stubLog))

# --- 5. Unsupported 3.13 only.
$d13 = Join-Path $root 'p313'; [void](New-Fake $d13 'python' '3.13.1|1|C:\Python313\python.exe')
Use-Path @($d13); $r = Find-Python ''
Check '3.13 only: rejected' ($null -eq $r)
Check '3.13 only: diagnostic says why' (($script:PyDiag | Where-Object { $_.Result -like '*only 3.11 and 3.12 are supported*' }).Count -ge 1)

# --- 6. 32-bit.
$d32 = Join-Path $root 'p32'; [void](New-Fake $d32 'python' '3.11.9|0|C:\Python311-32\python.exe')
Use-Path @($d32); $r = Find-Python ''
Check '32-bit 3.11: rejected with a 64-bit message' (($null -eq $r) -and (($script:PyDiag | Where-Object { $_.Result -like '*32-bit*' }).Count -ge 1))

# --- 7. Nothing at all.
$empty = Join-Path $root 'empty'; New-Item -ItemType Directory $empty | Out-Null
Use-Path @($empty); $r = Find-Python ''
Check 'no python anywhere: not found, with entries explaining each place searched' (($null -eq $r) -and $script:PyDiag.Count -ge 3)

# --- 8. py exists but has no 3.11 (e.g. only 3.13 registered): falls through to python on PATH.
$mixed = Join-Path $root 'mixed'; [void](New-Fake $mixed 'py' '' 103)
Use-Path @($mixed, $real_dir); $r = Find-Python ''
Check 'py launcher lacks 3.11/3.12: falls through to python on PATH' ($r -and $r.Full -eq '3.11.9' -and $r.Via -like 'PATH*')

# --- 9. Explicit -PythonPath wins, and a bad one falls back to detection rather than failing.
Use-Path @($real_dir)
$exp = Join-Path $root 'explicit'; [void](New-Fake $exp 'python' '3.12.4|1|C:\Python312\python.exe')
$r = Find-Python (Join-Path $exp 'python')
Check '-PythonPath is honoured' ($r -and $r.Full -eq '3.12.4' -and $r.Via -eq '-PythonPath parameter')
$r = Find-Python (Join-Path $root 'does-not-exist')
Check 'bad -PythonPath: recorded as missing, auto-detection still succeeds' ($r -and $r.Full -eq '3.11.9' -and (($script:PyDiag | Where-Object { $_.Result -eq 'file does not exist' }).Count -eq 1))

# --- 10. The Windows-style alias path test itself (the real function, real Windows paths).
. ([scriptblock]::Create(($ast.FindAll({ param($n) $n -is [System.Management.Automation.Language.FunctionDefinitionAst] -and $n.Name -eq 'Test-StoreAliasPath' }, $true) | Select-Object -First 1).Extent.Text))
Check 'alias path test: matches the real WindowsApps alias' (Test-StoreAliasPath 'C:\Users\Ujjawal Keshri\AppData\Local\Microsoft\WindowsApps\python.exe')
Check 'alias path test: matches python3.exe alias' (Test-StoreAliasPath 'C:\Users\x\AppData\Local\Microsoft\WindowsApps\python3.exe')
Check 'alias path test: does NOT match python.org install' (-not (Test-StoreAliasPath 'C:\Users\Ujjawal Keshri\AppData\Local\Programs\Python\Python311\python.exe'))

Remove-Item -Recurse -Force $root
Write-Host ''
if ($script:fail -eq 0) { Write-Host "ALL PASSED ($Mode)" -ForegroundColor Green; exit 0 } else { Write-Host "$($script:fail) FAILED ($Mode)" -ForegroundColor Red; exit 1 }
