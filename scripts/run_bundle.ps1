param(
    [string]$Python = "python"
)

$ErrorActionPreference = "Stop"
$captureDll = Join-Path $PSScriptRoot "native\uga_capture.dll"
if (-not (Test-Path -LiteralPath $captureDll -PathType Leaf)) {
    throw "Native capture library not found: $captureDll"
}

$env:UGA_NATIVE_CAPTURE_DLL = (Resolve-Path -LiteralPath $captureDll).Path
& $Python -m apps.agent
