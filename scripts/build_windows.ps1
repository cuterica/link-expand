param([string]$Output = "")
$ErrorActionPreference = "Stop"
$SourceRoot = Split-Path $PSScriptRoot -Parent
$BuildEnv = Join-Path $env:LOCALAPPDATA "LinkExpand\build-venv"
$BuildPython = Join-Path $BuildEnv "Scripts\python.exe"
if (!(Test-Path $BuildPython)) {
    & py -3 -m venv $BuildEnv
    if ($LASTEXITCODE -ne 0) { throw "Could not create build environment." }
}
$env:PYTHONUTF8 = "1"
& $BuildPython -m pip install -r (Join-Path $SourceRoot "requirements-build.txt")
if ($LASTEXITCODE -ne 0) { throw "Could not install build dependencies." }
$Arguments = @((Join-Path $PSScriptRoot "build_windows.py"))
if ($Output) { $Arguments += @("--output", $Output) }
& $BuildPython @Arguments
if ($LASTEXITCODE -ne 0) { throw "Windows build failed." }
