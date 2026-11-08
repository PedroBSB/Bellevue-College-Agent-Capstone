# bcagent installer for Windows (PowerShell 5+)
# Installs everything that is missing: Python 3, Ollama, the bcagent CLI and the default local model.
#
#   powershell -ExecutionPolicy Bypass -File install.ps1            # full install
#   powershell -ExecutionPolicy Bypass -File install.ps1 -NoModel   # skip model download
#   powershell -ExecutionPolicy Bypass -File install.ps1 -Uninstall
param([switch]$NoModel, [switch]$Uninstall)

$ErrorActionPreference = "Stop"
$ProgressPreference = "SilentlyContinue"   # makes Invoke-WebRequest much faster
[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12

$RepoZip     = "https://github.com/PedroBSB/Bellevue-College-Agent-Capstone/archive/refs/heads/main.zip"
$BcHome      = if ($env:BCAGENT_HOME) { $env:BCAGENT_HOME } else { Join-Path $env:USERPROFILE ".bcagent" }
$Model       = if ($env:BCAGENT_MODEL) { $env:BCAGENT_MODEL } else { "qwen2.5-coder:0.5b" }
$BinDir      = Join-Path $BcHome "bin"
$ScriptDir   = if ($PSScriptRoot) { $PSScriptRoot } else { (Get-Location).Path }
$PyVersion   = "3.12.8"

function Say($m)  { Write-Host "==> " -ForegroundColor Cyan -NoNewline; Write-Host $m }
function Ok($m)   { Write-Host "  v " -ForegroundColor Green -NoNewline; Write-Host $m }
function Warn($m) { Write-Host "  ! $m" -ForegroundColor Yellow }
function Die($m)  { Write-Host "  x $m" -ForegroundColor Red; exit 1 }

function Banner {
  $art = @'

     ,-.             ,-.
    ( ( `.  _____  .' ) )   bcagent
     \ \  \/     \/  / /    installer
      `.\  _     _  /,'
        | (o)   (o) |
        |    ___    |    BELLEVUE
       /|   (___)   |\   COLLEGE
      | \ \_______/ / |
       \ '.\/\/\/\/.' /
        '.__/~~~\__.'

'@
  Write-Host $art -ForegroundColor DarkBlue -BackgroundColor White
}

function Refresh-Path {
  $env:Path = [Environment]::GetEnvironmentVariable("Path", "Machine") + ";" +
              [Environment]::GetEnvironmentVariable("Path", "User")
}

if ($Uninstall) {
  Say "Removing bcagent"
  Remove-Item -Recurse -Force (Join-Path $BcHome "venv") -ErrorAction SilentlyContinue
  Remove-Item -Recurse -Force $BinDir -ErrorAction SilentlyContinue
  $userPath = [Environment]::GetEnvironmentVariable("Path", "User")
  $newPath = ($userPath -split ";" | Where-Object { $_ -and $_ -ne $BinDir }) -join ";"
  [Environment]::SetEnvironmentVariable("Path", $newPath, "User")
  Ok "bcagent removed (config kept in $BcHome). Ollama and models were not touched."
  exit 0
}

Banner
Say "Installing bcagent for Windows ($env:PROCESSOR_ARCHITECTURE)"

# ------------------------------------------------------------------ 1. Python
function Find-Python {
  $candidates = @()
  if (Get-Command py -ErrorAction SilentlyContinue) { $candidates += ,@("py", "-3") }
  foreach ($n in "python", "python3") {
    $c = Get-Command $n -ErrorAction SilentlyContinue
    # skip the Microsoft Store "python.exe" alias stub
    if ($c -and $c.Source -notlike "*WindowsApps*") { $candidates += ,@($c.Source) }
  }
  foreach ($dir in Get-ChildItem "$env:LOCALAPPDATA\Programs\Python\Python3*", "$env:ProgramFiles\Python3*" -ErrorAction SilentlyContinue) {
    $exe = Join-Path $dir.FullName "python.exe"
    if (Test-Path $exe) { $candidates += ,@($exe) }
  }
  foreach ($cand in $candidates) {
    try {
      $exe = $cand[0]; $pyArgs = @($cand | Select-Object -Skip 1)
      & $exe @pyArgs -c "import sys, venv; sys.exit(0 if sys.version_info >= (3, 9) else 1)" 2>$null
      if ($LASTEXITCODE -eq 0) {
        return (& $exe @pyArgs -c "import sys; print(sys.executable)").Trim()
      }
    } catch { }
  }
  return $null
}

$Py = Find-Python
if (-not $Py) {
  Say "Python 3.9+ not found - installing Python $PyVersion"
  $installed = $false
  if (Get-Command winget -ErrorAction SilentlyContinue) {
    try {
      winget install --id Python.Python.3.12 -e --scope user --silent --accept-package-agreements --accept-source-agreements
      $installed = $true
    } catch { Warn "winget failed, using the python.org installer" }
  }
  if (-not $installed -or -not (Find-Python)) {
    $arch = if ($env:PROCESSOR_ARCHITECTURE -eq "ARM64") { "arm64" } else { "amd64" }
    $exe = Join-Path $env:TEMP "python-$PyVersion-$arch.exe"
    Invoke-WebRequest "https://www.python.org/ftp/python/$PyVersion/python-$PyVersion-$arch.exe" -OutFile $exe
    Start-Process $exe -Wait -ArgumentList "/quiet", "InstallAllUsers=0", "PrependPath=1", "Include_pip=1", "Include_launcher=1"
    Remove-Item $exe -ErrorAction SilentlyContinue
  }
  Refresh-Path
  $Py = Find-Python
  if (-not $Py) { Die "Python was installed but not found - open a new terminal and re-run install.bat" }
}
Ok "Python: $Py"

# ------------------------------------------------------------------ 2. bcagent
Say "Creating virtual environment in $BcHome\venv"
New-Item -ItemType Directory -Force -Path $BcHome | Out-Null
Remove-Item -Recurse -Force (Join-Path $BcHome "venv") -ErrorAction SilentlyContinue
& $Py -m venv (Join-Path $BcHome "venv")
if ($LASTEXITCODE -ne 0) { Die "could not create a virtual environment" }
$VPy = Join-Path $BcHome "venv\Scripts\python.exe"
& $VPy -m pip install --quiet --upgrade pip
if ((Test-Path (Join-Path $ScriptDir "pyproject.toml")) -and (Test-Path (Join-Path $ScriptDir "bcagent"))) {
  Say "Installing bcagent and its requirements from $ScriptDir"
  & $VPy -m pip install --quiet --upgrade $ScriptDir
} else {
  Say "Installing bcagent and its requirements from GitHub"
  & $VPy -m pip install --quiet --upgrade $RepoZip
}
if ($LASTEXITCODE -ne 0) { Die "pip install failed" }

New-Item -ItemType Directory -Force -Path $BinDir | Out-Null
$shim = "@echo off`r`n`"$BcHome\venv\Scripts\bcagent.exe`" %*`r`n"
Set-Content -Path (Join-Path $BinDir "bcagent.cmd") -Value $shim -Encoding ASCII
$userPath = [Environment]::GetEnvironmentVariable("Path", "User")
if (-not $userPath) { $userPath = "" }
if (($userPath -split ";") -notcontains $BinDir) {
  [Environment]::SetEnvironmentVariable("Path", ($userPath.TrimEnd(";") + ";" + $BinDir).TrimStart(";"), "User")
  Ok "added $BinDir to your PATH"
}
$env:Path = "$BinDir;$env:Path"
Ok "bcagent installed -> $BinDir\bcagent.cmd"

# ------------------------------------------------------------------ 3. Ollama
function Find-Ollama {
  $c = Get-Command ollama -ErrorAction SilentlyContinue
  if ($c) { return $c.Source }
  $p = Join-Path $env:LOCALAPPDATA "Programs\Ollama\ollama.exe"
  if (Test-Path $p) { return $p }
  return $null
}

$Ollama = Find-Ollama
if (-not $Ollama) {
  Say "Installing Ollama (local LLM runtime)"
  $done = $false
  if (Get-Command winget -ErrorAction SilentlyContinue) {
    try { winget install --id Ollama.Ollama -e --silent --accept-package-agreements --accept-source-agreements; $done = $true } catch { }
  }
  if (-not $done -or -not (Find-Ollama)) {
    $setup = Join-Path $env:TEMP "OllamaSetup.exe"
    Invoke-WebRequest "https://ollama.com/download/OllamaSetup.exe" -OutFile $setup
    Start-Process $setup -Wait -ArgumentList "/VERYSILENT", "/NORESTART", "/SUPPRESSMSGBOXES"
    Remove-Item $setup -ErrorAction SilentlyContinue
  }
  Refresh-Path
  $env:Path = "$BinDir;$env:Path"
  $Ollama = Find-Ollama
  if (-not $Ollama) { Die "Ollama install failed - install it from https://ollama.com/download and re-run" }
}
Ok "Ollama: $Ollama"

function Ollama-Up { try { Invoke-RestMethod "http://127.0.0.1:11434/api/version" -TimeoutSec 2 | Out-Null; return $true } catch { return $false } }
if (-not (Ollama-Up)) {
  Say "Starting the Ollama server"
  Start-Process $Ollama -ArgumentList "serve" -WindowStyle Hidden
  for ($i = 0; $i -lt 40 -and -not (Ollama-Up); $i++) { Start-Sleep -Milliseconds 500 }
}
if (Ollama-Up) { Ok "Ollama server running" } else { Warn "Ollama server not responding yet - bcagent will try to start it" }

# ------------------------------------------------------------------ 4. model
if (-not $NoModel) {
  Say "Downloading the default model $Model (~400 MB, smallest & fastest local code LLM)"
  & $Ollama pull $Model
  if ($LASTEXITCODE -eq 0) { Ok "model $Model ready" } else { Warn "model download failed - it will download on first run" }
}

Write-Host ""
Write-Host "  bcagent is ready!  " -ForegroundColor White -BackgroundColor DarkBlue
Write-Host ""
Write-Host "  Open a NEW terminal, cd into a project folder and run:  bcagent"
Write-Host "  One-shot mode:   bcagent -p `"write a fizzbuzz script in fizzbuzz.py`" --yes"
Write-Host "  More agents:     bcagent agents catalog"
