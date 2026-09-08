[CmdletBinding()]
param(
    [ValidateSet("debug", "release")]
    [string]$Profile = "release"
)

$ErrorActionPreference = "Stop"
$projectRoot = Split-Path -Parent $PSScriptRoot
$manifest = Join-Path $projectRoot "native\Cargo.toml"
$arguments = @("build", "--manifest-path", $manifest, "-p", "uga-capture")
if ($Profile -eq "release") {
    $arguments += "--release"
}

& cargo @arguments
if ($LASTEXITCODE -ne 0) {
    throw "Native build failed with exit code $LASTEXITCODE"
}

$dll = Join-Path $projectRoot "native\target\$Profile\uga_capture.dll"
if (-not (Test-Path -LiteralPath $dll)) {
    throw "Native capture DLL was not produced at $dll"
}
Write-Output $dll
