Set-Location -LiteralPath $PSScriptRoot
& "$PSScriptRoot\build.cmd"
exit $LASTEXITCODE
