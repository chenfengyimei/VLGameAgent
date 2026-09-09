param(
    [string]$Python = "python"
)

$ErrorActionPreference = "Stop"
$captureDll = Join-Path $PSScriptRoot "native\uga_capture.dll"
$manifestPath = Join-Path $PSScriptRoot "release-manifest.json"
if (-not (Test-Path -LiteralPath $captureDll -PathType Leaf)) {
    throw "Native capture library not found: $captureDll"
}
if (-not (Test-Path -LiteralPath $manifestPath -PathType Leaf)) {
    throw "Release manifest not found: $manifestPath"
}
$manifest = Get-Content -LiteralPath $manifestPath -Raw | ConvertFrom-Json
$expectedDigest = $manifest.artifacts.'native/uga_capture.dll'
if (-not $expectedDigest -or $expectedDigest -notmatch '^[0-9a-f]{64}$') {
    throw "Release manifest has no trusted native capture digest"
}
$actualDigest = (Get-FileHash -Algorithm SHA256 -LiteralPath $captureDll).Hash.ToLowerInvariant()
if ($actualDigest -cne $expectedDigest) {
    throw "Native capture library digest mismatch"
}

$env:UGA_NATIVE_CAPTURE_DLL = (Resolve-Path -LiteralPath $captureDll).Path
$env:UGA_NATIVE_CAPTURE_SHA256 = $expectedDigest
& $Python -m apps.agent
