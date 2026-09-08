param(
    [string]$Python = "python",
    [string]$BundleName = "uga-0.1.0-dev-windows-x64"
)

$ErrorActionPreference = "Stop"
$projectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$nativeRoot = Join-Path $projectRoot "native"
$distRoot = Join-Path $projectRoot "dist"
$bundleRoot = Join-Path $distRoot $BundleName

if (Test-Path -LiteralPath $bundleRoot) {
    throw "Release bundle already exists: $bundleRoot"
}

Push-Location $projectRoot
try {
    npm ci
    if ($LASTEXITCODE -ne 0) { throw "npm install failed with exit code $LASTEXITCODE" }
    npm run typecheck
    if ($LASTEXITCODE -ne 0) { throw "TypeScript typecheck failed with exit code $LASTEXITCODE" }
    npm run build
    if ($LASTEXITCODE -ne 0) { throw "TypeScript build failed with exit code $LASTEXITCODE" }
    & $Python -m ruff check .
    if ($LASTEXITCODE -ne 0) { throw "Ruff failed with exit code $LASTEXITCODE" }
    & $Python -m mypy uga apps
    if ($LASTEXITCODE -ne 0) { throw "mypy failed with exit code $LASTEXITCODE" }
    & $Python -m pytest
    if ($LASTEXITCODE -ne 0) { throw "pytest failed with exit code $LASTEXITCODE" }
    & $Python -m build
    if ($LASTEXITCODE -ne 0) { throw "Python build failed with exit code $LASTEXITCODE" }
    Push-Location $nativeRoot
    try {
        cargo build --workspace --release
        if ($LASTEXITCODE -ne 0) { throw "Cargo build failed with exit code $LASTEXITCODE" }
    }
    finally {
        Pop-Location
    }
    New-Item -ItemType Directory -Path $bundleRoot | Out-Null
    New-Item -ItemType Directory -Path (Join-Path $bundleRoot "native") | Out-Null
    Copy-Item -Path (Join-Path $distRoot "*.whl") -Destination $bundleRoot
    Copy-Item -Path (Join-Path $distRoot "*.tar.gz") -Destination $bundleRoot
    Copy-Item -LiteralPath (Join-Path $nativeRoot "target\release\uga_capture.dll") `
        -Destination (Join-Path $bundleRoot "native\uga_capture.dll")
    Copy-Item -LiteralPath (Join-Path $projectRoot "configs") -Destination $bundleRoot -Recurse
    Copy-Item -LiteralPath (Join-Path $projectRoot "docs") -Destination $bundleRoot -Recurse
    Copy-Item -LiteralPath (Join-Path $projectRoot "third_party") -Destination $bundleRoot -Recurse
    foreach ($name in @("README.md", "ARCHITECTURE.md", "SECURITY.md", "RELEASE_CHECKLIST.md")) {
        Copy-Item -LiteralPath (Join-Path $projectRoot $name) -Destination $bundleRoot
    }
    Copy-Item -LiteralPath (Join-Path $projectRoot "scripts\run_bundle.ps1") `
        -Destination (Join-Path $bundleRoot "run_uga.ps1")
    & $Python -m apps.dependency_inventory `
        --pyproject (Join-Path $projectRoot "pyproject.toml") `
        --cargo-manifest (Join-Path $nativeRoot "Cargo.toml") `
        --npm-lock (Join-Path $projectRoot "package-lock.json") `
        --output (Join-Path $bundleRoot "third-party-inventory.json")
    if ($LASTEXITCODE -ne 0) { throw "Dependency inventory failed with exit code $LASTEXITCODE" }
    & $Python -m apps.release_manifest $bundleRoot
    if ($LASTEXITCODE -ne 0) { throw "Release manifest failed with exit code $LASTEXITCODE" }
    Write-Output $bundleRoot
}
finally {
    Pop-Location
}
