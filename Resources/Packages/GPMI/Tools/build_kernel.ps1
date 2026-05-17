param(
  [string]$SourceDir = "$PSScriptRoot\kernel_source",
  [string]$OutDir = "$PSScriptRoot\..\Core\GPMI"
)

$ErrorActionPreference = "Stop"

if (-not (Test-Path -LiteralPath (Join-Path $SourceDir "build.cmd"))) {
  throw "build.cmd not found: $SourceDir"
}

Push-Location $SourceDir
try {
  & cmd.exe /c build.cmd
  if ($LASTEXITCODE -ne 0) {
    throw "build.cmd failed with exit code $LASTEXITCODE"
  }
}
finally {
  Pop-Location
}
