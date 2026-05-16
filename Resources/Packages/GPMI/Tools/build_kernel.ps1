param(
  [string]$SourceDir = "$PSScriptRoot\kernel_source",
  [string]$BuildDir = "$PSScriptRoot\kernel_build",
  [string]$OutDir = "$PSScriptRoot\..\Core\GPMI"
)

$ErrorActionPreference = "Stop"
New-Item -ItemType Directory -Force -Path $BuildDir | Out-Null
New-Item -ItemType Directory -Force -Path $OutDir | Out-Null
cmake -S $SourceDir -B $BuildDir -A x64 -DCMAKE_BUILD_TYPE=Release
cmake --build $BuildDir --config Release
$built = Get-ChildItem -Path $BuildDir -Recurse -Filter "*.addon64" | Select-Object -First 1
if (-not $built) { $built = Get-ChildItem -Path $BuildDir -Recurse -Filter "*.dll" | Where-Object { $_.Name -like "*Portrait*" } | Select-Object -First 1 }
if (-not $built) { throw "Built add-on not found." }
Copy-Item $built.FullName (Join-Path $OutDir "PortraitHashReplace.addon64") -Force
New-Item -ItemType Directory -Force -Path (Join-Path $OutDir "Addons") | Out-Null
Copy-Item $built.FullName (Join-Path $OutDir "Addons\PortraitHashReplace.addon64") -Force
Write-Host "Copied $($built.FullName) -> $(Join-Path $OutDir 'PortraitHashReplace.addon64')"
Write-Host "Copied $($built.FullName) -> $(Join-Path $OutDir 'Addons\PortraitHashReplace.addon64')"
