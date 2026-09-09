param(
    [string]$Python = "python",
    [string]$BundleName = "uga-0.1.0-dev-windows-x64"
)

$ErrorActionPreference = "Stop"
$projectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$nativeRoot = Join-Path $projectRoot "native"
$distRoot = Join-Path $projectRoot "dist"
$bundleRoot = Join-Path $distRoot $BundleName
$sourceRevision = (& git -C $projectRoot rev-parse HEAD).Trim()
if ($LASTEXITCODE -ne 0 -or -not $sourceRevision) {
    throw "Unable to resolve the source Git revision"
}
$dirty = & git -C $projectRoot status --porcelain
if ($LASTEXITCODE -ne 0) { throw "Unable to inspect the source worktree" }
if ($dirty) { throw "Release builds require a clean committed worktree" }

if (Test-Path -LiteralPath $bundleRoot) {
    throw "Release bundle already exists: $bundleRoot"
}

$buildVenvRoot = $null

Push-Location $projectRoot
try {
    npm ci --include=dev
    if ($LASTEXITCODE -ne 0) { throw "npm ci failed with exit code $LASTEXITCODE" }
    npm run typecheck
    if ($LASTEXITCODE -ne 0) { throw "TypeScript typecheck failed with exit code $LASTEXITCODE" }
    npm run build
    if ($LASTEXITCODE -ne 0) { throw "TypeScript build failed with exit code $LASTEXITCODE" }
    $buildVenvRoot = Join-Path $distRoot (".uga-build-venv-" + [guid]::NewGuid().ToString("N"))
    & $Python -m venv $buildVenvRoot
    if ($LASTEXITCODE -ne 0) { throw "Build virtual environment creation failed" }
    $buildPython = Join-Path $buildVenvRoot "Scripts\python.exe"
    & $buildPython -m pip install --require-hashes -r (Join-Path $projectRoot "requirements-lock.txt")
    if ($LASTEXITCODE -ne 0) { throw "Locked dependency installation failed with exit code $LASTEXITCODE" }
    & $buildPython -m pip install --no-deps --no-build-isolation -e $projectRoot
    if ($LASTEXITCODE -ne 0) { throw "Project installation failed with exit code $LASTEXITCODE" }
    & $buildPython -m ruff check .
    if ($LASTEXITCODE -ne 0) { throw "Ruff failed with exit code $LASTEXITCODE" }
    & $buildPython -m mypy uga apps
    if ($LASTEXITCODE -ne 0) { throw "mypy failed with exit code $LASTEXITCODE" }
    & $buildPython -m pytest
    if ($LASTEXITCODE -ne 0) { throw "pytest failed with exit code $LASTEXITCODE" }
    & $buildPython -m build --no-isolation
    if ($LASTEXITCODE -ne 0) { throw "Python build failed with exit code $LASTEXITCODE" }
    Push-Location $nativeRoot
    try {
        cargo build --workspace --release --locked
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
    foreach ($name in @("LICENSE", "README.md", "ARCHITECTURE.md", "SECURITY.md", "RELEASE_CHECKLIST.md")) {
        Copy-Item -LiteralPath (Join-Path $projectRoot $name) -Destination $bundleRoot
    }
    Copy-Item -LiteralPath (Join-Path $projectRoot "scripts\run_bundle.ps1") `
        -Destination (Join-Path $bundleRoot "run_uga.ps1")
    & $buildPython -m apps.dependency_inventory `
        --pyproject (Join-Path $projectRoot "pyproject.toml") `
        --cargo-manifest (Join-Path $nativeRoot "Cargo.toml") `
        --npm-lock (Join-Path $projectRoot "package-lock.json") `
        --output (Join-Path $bundleRoot "third-party-inventory.json")
    if ($LASTEXITCODE -ne 0) { throw "Dependency inventory failed with exit code $LASTEXITCODE" }
    $smokeRoot = Join-Path $distRoot (".uga-install-smoke-" + [guid]::NewGuid().ToString("N"))
    try {
        & $Python -m venv $smokeRoot
        if ($LASTEXITCODE -ne 0) { throw "Clean venv creation failed with exit code $LASTEXITCODE" }
        $smokePython = Join-Path $smokeRoot "Scripts\python.exe"
        $wheel = Get-ChildItem -LiteralPath $bundleRoot -Filter "*.whl" -File
        & $smokePython -m pip install --no-deps $wheel.FullName
        if ($LASTEXITCODE -ne 0) { throw "Wheel installation failed with exit code $LASTEXITCODE" }
        $env:UGA_NATIVE_CAPTURE_DLL = (Resolve-Path -LiteralPath (
            Join-Path $bundleRoot "native\uga_capture.dll"
        )).Path
        $env:UGA_NATIVE_CAPTURE_SHA256 = (Get-FileHash -Algorithm SHA256 -LiteralPath (
            Join-Path $bundleRoot "native\uga_capture.dll"
        )).Hash.ToLowerInvariant()
        foreach ($command in @(
            @{ Name = "uga-agent.exe"; Arguments = @() },
            @{ Name = "uga-example-game.exe"; Arguments = @("--headless-smoke") },
            @{ Name = "uga-capture-probe.exe"; Arguments = @("--help") },
            @{ Name = "uga-dataset.exe"; Arguments = @("--help") },
            @{ Name = "uga-benchmark.exe"; Arguments = @("--help") },
            @{ Name = "uga-qualify.exe"; Arguments = @("--help") },
            @{ Name = "uga-train.exe"; Arguments = @("--help") }
        )) {
            $executable = Join-Path $smokeRoot ("Scripts\" + $command.Name)
            & $executable @($command.Arguments)
            if ($LASTEXITCODE -ne 0) {
                throw "$($command.Name) smoke failed with exit code $LASTEXITCODE"
            }
        }
    }
    finally {
        if (Test-Path -LiteralPath $smokeRoot) {
            $resolvedSmoke = (Resolve-Path -LiteralPath $smokeRoot).Path
            if ((Split-Path -Parent $resolvedSmoke) -ne $distRoot) {
                throw "Refusing to remove unexpected smoke directory: $resolvedSmoke"
            }
            Remove-Item -LiteralPath $resolvedSmoke -Recurse -Force
        }
    }
    & $buildPython -m apps.release_manifest $bundleRoot `
        --source-revision $sourceRevision `
        --package-smoke-passed
    if ($LASTEXITCODE -ne 0) { throw "Release manifest failed with exit code $LASTEXITCODE" }
    & $buildPython -m apps.release_manifest $bundleRoot --verify-existing
    if ($LASTEXITCODE -ne 0) { throw "Bundle verification failed with exit code $LASTEXITCODE" }
    $finalRevision = (& git -C $projectRoot rev-parse HEAD).Trim()
    $finalDirty = & git -C $projectRoot status --porcelain
    if ($LASTEXITCODE -ne 0) { throw "Unable to re-inspect the source worktree" }
    if ($finalRevision -cne $sourceRevision -or $finalDirty) {
        throw "Source worktree changed while building the release bundle"
    }
    Write-Output $bundleRoot
}
finally {
    Pop-Location
    if ($buildVenvRoot -and (Test-Path -LiteralPath $buildVenvRoot)) {
        $resolvedBuildVenv = (Resolve-Path -LiteralPath $buildVenvRoot).Path
        if ((Split-Path -Parent $resolvedBuildVenv) -ne $distRoot) {
            throw "Refusing to remove unexpected build venv directory: $resolvedBuildVenv"
        }
        Remove-Item -LiteralPath $resolvedBuildVenv -Recurse -Force
    }
}
