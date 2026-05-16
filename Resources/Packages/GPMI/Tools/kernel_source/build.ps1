$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot
& cmd.exe /c build.cmd
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
