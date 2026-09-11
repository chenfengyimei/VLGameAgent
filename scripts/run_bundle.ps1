param(
    [string]$Python = "python",
    [string]$ManifestSha256
)

$ErrorActionPreference = "Stop"

function Get-UgaSha256Hex {
    param([Parameter(Mandatory = $true)][string]$LiteralPath)

    $stream = [System.IO.File]::OpenRead($LiteralPath)
    try {
        $sha256 = [System.Security.Cryptography.SHA256]::Create()
        try {
            return [System.BitConverter]::ToString($sha256.ComputeHash($stream)).Replace("-", "").ToLowerInvariant()
        }
        finally {
            $sha256.Dispose()
        }
    }
    finally {
        $stream.Dispose()
    }
}

$captureDll = Join-Path $PSScriptRoot "native\uga_capture.dll"
$manifestPath = Join-Path $PSScriptRoot "release-manifest.json"
if (-not (Test-Path -LiteralPath $captureDll -PathType Leaf)) {
    throw "Native capture library not found: $captureDll"
}
if (-not (Test-Path -LiteralPath $manifestPath -PathType Leaf)) {
    throw "Release manifest not found: $manifestPath"
}
if ($ManifestSha256) {
    # Out-of-band anchor: the manifest digest is published separately, so the
    # manifest itself is verified before anything it declares can be trusted.
    if ($ManifestSha256 -notmatch '^[0-9a-f]{64}$') {
        throw "Manifest digest parameter must be a 64-character SHA-256"
    }
    $actualManifestDigest = Get-UgaSha256Hex -LiteralPath $manifestPath
    if ($actualManifestDigest -cne $ManifestSha256.ToLowerInvariant()) {
        throw "Release manifest digest mismatch against the out-of-band anchor"
    }
}
else {
    Write-Warning "No -ManifestSha256 anchor supplied: bundle verification is self-referential and cannot detect a bundle replaced together with its manifest."
}
& $Python -m apps.release_manifest $PSScriptRoot --verify-existing
if ($LASTEXITCODE -ne 0) {
    throw "Release bundle verification failed with exit code $LASTEXITCODE"
}
$manifest = Get-Content -LiteralPath $manifestPath -Raw | ConvertFrom-Json
$expectedDigest = $manifest.artifacts.'native/uga_capture.dll'
if (-not $expectedDigest -or $expectedDigest -notmatch '^[0-9a-f]{64}$') {
    throw "Release manifest has no trusted native capture digest"
}
$actualDigest = Get-UgaSha256Hex -LiteralPath $captureDll
if ($actualDigest -cne $expectedDigest) {
    throw "Native capture library digest mismatch"
}

$env:UGA_NATIVE_CAPTURE_DLL = (Resolve-Path -LiteralPath $captureDll).Path
$env:UGA_NATIVE_CAPTURE_SHA256 = $expectedDigest
& $Python -m apps.agent
